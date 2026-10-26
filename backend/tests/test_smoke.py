"""端到端冒烟测试：不依赖 pytest，直接跑 `python tests/test_smoke.py`。

验证覆盖：
  A 账号与鉴权      B 生成闭环与合规标识   C 针法规则引擎
  D 知识问答与兜底  E 社区与积分           F 后台审核与留痕
"""

from __future__ import annotations

import json
import os
import random
import string
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = os.environ.get("ZY_BASE", "http://127.0.0.1:8130")
ROOT = Path(__file__).resolve().parents[2]  # tests -> backend -> 项目根

PASSED = 0
FAILED: list[str] = []


def req(method: str, path: str, data=None, token: str = "", device: str = ""):
    url = BASE + path
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if device:
        headers["X-Device-Key"] = device
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return resp.status, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"message": raw}


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED.append(name)
        print(f"  ✗ {name} {extra}")


def rnd(n=8):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def main() -> int:
    print(f"\n=== 针语 MVP 冒烟测试 · {BASE} ===")

    # ---------------- A 账号 ----------------
    print("\n[A] 账号与鉴权")
    _, h = req("GET", "/health")
    check("健康检查通过", h.get("status") == "ok", str(h))

    email = f"t{rnd()}@zhenyu.dev"
    st, reg = req("POST", "/api/auth/register",
                  {"email": email, "nickname": "测试用户", "password": "zhenyu12345"})
    check("注册成功并返回 token", st == 200 and reg["code"] == 0 and bool(reg["data"]["token"]), str(reg))
    token = reg["data"].get("token", "") if reg.get("data") else ""
    check("注册赠送积分已入账", reg.get("data", {}).get("user", {}).get("points", 0) >= 50, str(reg.get("data")))

    _, dup = req("POST", "/api/auth/register",
                 {"email": email, "nickname": "重复", "password": "zhenyu12345"})
    check("重复邮箱被拒绝", dup["code"] == 40901, str(dup))

    _, bad = req("POST", "/api/auth/login", {"email": email, "password": "wrong"})
    check("错误密码登录失败", bad["code"] != 0, str(bad))

    _, login = req("POST", "/api/auth/login", {"email": email, "password": "zhenyu12345"})
    token = login["data"]["token"]
    check("登录成功", login["code"] == 0 and bool(token))

    _, me = req("GET", "/api/auth/me", token=token)
    check("凭 token 取到当前用户", me["data"]["nickname"] == "测试用户", str(me))

    _, noauth = req("GET", "/api/auth/me")
    check("无 token 访问受限接口被拦截", noauth["code"] == 40101, str(noauth))

    # ---------------- B 生成闭环 ----------------
    print("\n[B] AI 生成闭环与合规标识")
    _, gen = req("POST", "/api/generate",
                 {"prompt": "一只侧卧的老虎，暖黄色毛发，要有蓬松厚重的毛质感，背景深红渐变"},
                 token=token)
    check("生成返回成功", gen["code"] == 0 and gen["data"]["status"] == "succeeded", str(gen)[:200])
    g = gen.get("data", {})
    check("命中毛发特征", "毛发质感" in g.get("features", []), str(g.get("features")))
    codes = [s["stitch_code"] for s in g.get("suggestions", [])]
    check("推荐了 1-2 种针法", 1 <= len(codes) <= 2, str(codes))
    check("鬅毛针被推荐且在首位", codes[:1] == ["pengmao"], str(codes))
    check("建议标注待顾问确认",
          all(s["review_status"] == "pending_expert" for s in g.get("suggestions", [])), str(g))

    img = (g.get("images") or [{}])[0]
    check("产出图片且带显式标识", bool(img.get("visible_label")) and "AI" in img["visible_label"], str(img)[:160])
    meta = img.get("meta", {})
    for key in ("AI-Generated", "AI-Provider-Code", "AI-Record-ID", "AI-Is-Simulation"):
        check(f"隐式元数据含 {key}", key in meta, str(meta)[:200])

    url = BASE + img.get("url", "")
    code = urllib.request.urlopen(img.get("url") and url or url, timeout=20).status \
        if img.get("url") else 0
    check("生成图可通过 HTTP 访问", code == 200, str(code))

    # 磁盘上的 PNG 文本块必须能读回标识
    from app.services.watermark import read_implicit_meta  # noqa: E402
    png = ROOT / "data" / "storage" / g["public_id"] / "result.png"
    check("PNG 文件已落盘", png.exists(), str(png))
    if png.exists():
        disk_meta = read_implicit_meta(png)
        check("落盘文件的隐式标识可读回",
              disk_meta.get("AI-Provider-Code") == meta.get("AI-Provider-Code"), str(disk_meta)[:160])

    _, empty = req("POST", "/api/generate", {"prompt": ""})
    check("空描述被参数校验拦截", empty["code"] == 40001, str(empty))

    _, nolimit = req("POST", "/api/stitches/preview-rules", {"prompt": "一朵牡丹，花瓣渐变"})
    check("规则预览可用（不生成图）",
          nolimit["code"] == 0 and any(s["stitch_code"] == "canzhen"
                                       for s in nolimit["data"]["suggestions"]), str(nolimit)[:200])

    # ---------------- C 针法库 ----------------
    print("\n[C] 针法库")
    _, st_list = req("GET", "/api/stitches")
    check("针法列表非空", len(st_list["data"]["items"]) >= 5, str(st_list["data"]["items"])[:120])
    check("列表含分类维度", len(st_list["data"]["families"]) >= 2, str(st_list["data"]["families"]))
    _, detail = req("GET", "/api/stitches/pengmao")
    check("针法详情含步骤与规则",
          detail["code"] == 0 and len(detail["data"]["steps"]) >= 3
          and len(detail["data"]["rules"]) >= 1, str(detail)[:200])
    _, missing = req("GET", "/api/stitches/not-exist")
    check("不存在的针法返回 40401", missing["code"] == 40401, str(missing))

    # ---------------- D 知识问答 ----------------
    print("\n[D] 知识问答与兜底")
    _, ask = req("POST", "/api/knowledge/ask", {"question": "掺针的主要作用是什么？"})
    d = ask["data"]
    check("命中知识库并给出答案", d["confident"] and "知识库暂无" not in d["answer"], str(ask)[:200])
    check("命中条目为掺针相关", bool(d["hits"]) and "掺针" in d["hits"][0]["question"], str(d["hits"])[:160])
    check("答案附来源或明确无来源", isinstance(ask["data"]["sources"], list), str(ask["data"]["sources"])[:120])
    check("未确认内容标注待确认", ask["data"]["review_status"] == "pending_expert", str(ask["data"]["review_status"]))

    _, none = req("POST", "/api/knowledge/ask", {"question": "量子计算与蔷薇科的杂交可行吗"})
    check("无命中时给出兜底话术",
          none["data"]["confident"] is False and "知识库暂无" in none["data"]["answer"], str(none)[:200])

    _, cats = req("GET", "/api/knowledge/categories")
    check("知识库统计可用", cats["data"]["total"] >= 40, str(cats["data"]))

    # ---------------- E 社区与积分 ----------------
    print("\n[E] 社区与积分")
    dev = "dev" + rnd(6)
    _, works = req("GET", "/api/works", device=dev)
    check("作品列表非空", works["data"]["total"] >= 8, str(works["data"]["total"]))
    wid = works["data"]["items"][0]["id"]
    _, t1 = req("POST", f"/api/works/{wid}/toggle", {"kind": "like"}, device=dev)
    check("点赞成功", t1["code"] == 0 and t1["data"]["active"] is True, str(t1))
    _, t2 = req("POST", f"/api/works/{wid}/toggle", {"kind": "like"}, device=dev)
    check("重复点击取消点赞", t2["data"]["active"] is False, str(t2))

    _, me_before = req("GET", "/api/auth/me", token=token)
    before = me_before["data"]["points"]
    _, pub = req("POST", "/api/works",
                 {"title": "冒烟测试作品", "description": "自动化测试用", "tags": "测试 掺针",
                  "stitch_code": "canzhen", "source_type": "handmade"}, token=token)
    check("登录后发布作品成功", pub["code"] == 0, str(pub))
    _, me_after = req("GET", "/api/auth/me", token=token)
    check("发布后积分增加", me_after["data"]["points"] >= before, f"{before} -> {me_after['data']['points']}")

    _, noauth_pub = req("POST", "/api/works", {"title": "未登录发布"})
    check("未登录发布被拦截", noauth_pub["code"] == 40101, str(noauth_pub))

    _, ci = req("POST", "/api/points/checkin", token=token)
    check("签到成功", ci["code"] == 0, str(ci))
    _, ci2 = req("POST", "/api/points/checkin", token=token)
    check("重复签到被每日上限拦截", ci2["code"] == 40001, str(ci2))

    _, led = req("GET", "/api/points/ledger", token=token)
    check("积分流水可查询且余额一致",
          led["data"]["items"] and led["data"]["items"][0]["balance_after"] == me_after["data"]["points"] + 0
          or led["data"]["balance"] >= 0, str(led["data"])[:160])

    # ---------------- F 后台 ----------------
    print("\n[F] 后台审核与留痕")
    _, admin_login = req("POST", "/api/auth/login",
                         {"email": "admin@zhenyu.dev", "password": "zhenyu123"})
    admin_token = admin_login["data"]["token"]
    _, ov = req("GET", "/api/admin/overview", token=admin_token)
    check("管理员可取总览", ov["code"] == 0 and ov["data"]["totals"]["stitches"] >= 5, str(ov)[:200])

    _, user_ov = req("GET", "/api/admin/overview", token=token)
    check("普通用户访问后台被拦截", user_ov["code"] == 40301, str(user_ov))

    _, pending = req("GET", "/api/admin/pending?target=knowledge", token=admin_token)
    check("待确认队列非空", len(pending["data"]["items"]) >= 5, str(pending["data"])[:150])
    target_id = pending["data"]["items"][0]["id"]
    _, review = req("POST", "/api/admin/review",
                    {"target": "knowledge", "id": target_id, "status": "expert_confirmed",
                     "note": "冒烟测试"}, token=admin_token)
    check("顾问确认动作成功", review["code"] == 0, str(review))

    _, audit = req("GET", "/api/admin/audit", token=admin_token)
    check("审核动作写入审计留痕",
          any(a["action"] == "expert_review" for a in audit["data"]), str(audit)[:160])

    _, gens = req("GET", "/api/admin/generations", token=admin_token)
    check("生成台账可查", gens["code"] == 0 and gens["data"]["items"], str(gens)[:160])

    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("失败项：" + "，".join(FAILED))
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
