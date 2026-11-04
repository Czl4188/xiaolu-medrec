"""接口测试：覆盖鉴权、跨用户隔离、抽取、作答边界、幂等。"""

import uuid
from datetime import datetime

TRANSCRIPT = (
    "今天下午去了市第一人民医院消化内科。医生说还是慢性胃炎伴糜烂，比上次好一些。"
    "开四种药：奥美拉唑肠溶胶囊 20mg，每天一次，早饭前吃，吃14天；"
    "铝碳酸镁咀嚼片 1g，每天三次，饭后嚼服，吃14天；"
    "莫沙必利片 5mg，每天三次，饭前吃；"
    "嘱咐少吃辣的、别喝酒，两周后来复诊。青霉素过敏这个要记上。"
)


def _new_record(client, headers, client_id="v_test_001"):
    return client.post("/api/records", headers=headers, json={
        "client_id": client_id,
        "visit_at": "2026-11-02T20:37:00+08:00",
        "hospital": "市第一人民医院",
        "summary": "慢性胃炎伴糜烂",
        "transcript": TRANSCRIPT,
        "meds": [
            {"name": "奥美拉唑肠溶胶囊", "dose": "20mg", "freq": "每天一次", "timing": "饭前", "days": "14"},
            {"name": "铝碳酸镁咀嚼片", "dose": "1g", "freq": "每天三次", "timing": "饭后", "days": "14"},
        ],
        "follow_up": "两周后来复诊",
        "allergy": "青霉素过敏",
        "notes": ["少吃辣的"],
        "mode": "manual",
    })


# ————————————————— 鉴权 —————————————————
def test_health_endpoints(client):
    assert client.get("/healthz").json()["status"] == "ok"
    body = client.get("/readyz").json()
    assert body["checks"]["database"] == "ok"


def test_requires_token(client):
    assert client.get("/api/records").status_code == 401
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/records", headers={"Authorization": "Bearer bad.token.here"}).status_code == 401


def test_login_wrong_password(client):
    r = client.post("/api/auth/login", json={"phone": "13800000000", "password": "wrong-pass"})
    assert r.status_code == 401
    # 错误信息不应泄露「用户是否存在」
    assert "手机号或密码" in r.json()["detail"]


# ————————————————— 隔离 —————————————————
def test_records_isolated_between_users(client, auth_headers, other_headers):
    created = _new_record(client, auth_headers, "v_iso_001")
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]

    # 本人可见
    assert client.get(f"/api/records/{record_id}", headers=auth_headers).status_code == 200

    # 他人：列表为空、按 id 读不到、删不掉
    assert client.get("/api/records", headers=other_headers).json() == []
    assert client.get(f"/api/records/{record_id}", headers=other_headers).status_code == 404
    assert client.delete(f"/api/records/{record_id}", headers=other_headers).status_code == 404
    # 原记录仍在
    assert client.get(f"/api/records/{record_id}", headers=auth_headers).status_code == 200


# ————————————————— 抽取 —————————————————
def test_extract_fields(client, auth_headers):
    r = client.post("/api/records/extract", headers=auth_headers, json={"text": TRANSCRIPT})
    assert r.status_code == 200
    data = r.json()
    assert data["hospital"] == "市第一人民医院"
    assert data["summary"] == "慢性胃炎伴糜烂"
    assert data["follow_up"] == "两周后来复诊"
    assert data["allergy"] == "青霉素过敏"
    assert [m["name"] for m in data["meds"]][:2] == ["奥美拉唑肠溶胶囊", "铝碳酸镁咀嚼片"]
    assert data["meds"][0]["days"] == "14"
    assert data["holes"] == []


def test_extract_does_not_invent(client, auth_headers):
    """否定句不该产出字段——这是「不编」的底线。"""
    text = "医生让我饭后服用一片，先吃一周，没说是啥病，也没提复诊。"
    data = client.post("/api/records/extract", headers=auth_headers, json={"text": text}).json()
    assert data["summary"] == ""
    assert data["follow_up"] == ""
    assert data["meds"] == []
    assert set(data["holes"]) >= {"诊断", "复诊"}


# ————————————————— 幂等 —————————————————
def test_record_create_is_idempotent(client, auth_headers):
    first = _new_record(client, auth_headers, "v_idem_001")
    second = _new_record(client, auth_headers, "v_idem_001")
    assert first.json()["id"] == second.json()["id"]


# ————————————————— 作答边界 —————————————————
def test_chat_answers_with_citation(client, auth_headers):
    _new_record(client, auth_headers, "v_chat_001")
    r = client.post("/api/chat/sync", headers=auth_headers, json={"question": "我今天要吃什么药？"})
    body = r.json()
    assert body["refused"] is False
    assert body["citations"], "医疗类回答必须带来源"
    assert "奥美拉唑" in body["answer"]


def test_chat_refuses_advice(client, auth_headers):
    r = client.post("/api/chat/sync", headers=auth_headers, json={"question": "我能不能吃海鲜？"})
    body = r.json()
    assert body["refused"] is True
    assert body["citations"] == []
    assert "不能答" in body["answer"]


def test_chat_refuses_when_not_in_records(client, auth_headers):
    r = client.post("/api/chat/sync", headers=auth_headers,
                    json={"question": "我上次做心脏支架是什么时候？"})
    body = r.json()
    assert body["refused"] is True
    assert "未查询到" in body["answer"]


def test_chat_allows_small_talk(client, auth_headers):
    body = client.post("/api/chat/sync", headers=auth_headers,
                       json={"question": "你叫什么名字？"}).json()
    assert body["refused"] is False
    assert "小鹿" in body["answer"]


# ————————————————— SSE —————————————————
def test_chat_stream_events(client, auth_headers):
    with client.stream("POST", "/api/chat", headers=auth_headers,
                       json={"question": "什么时候复诊？"}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = [line.split(":", 1)[1].strip()
                  for line in resp.iter_lines() if line.startswith("event:")]
    assert events[0] == "meta"
    assert events[-1] == "done"
    assert "delta" in events


# ————————————————— 长任务 —————————————————
def test_reindex_enqueues_run(client, auth_headers):
    record = _new_record(client, auth_headers, "v_job_001").json()
    r = client.post(f"/api/records/{record['id']}/reindex", headers=auth_headers)
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    detail = client.get(f"/api/runs/{run_id}", headers=auth_headers).json()
    assert detail["status"] in ("queued", "running", "ok")


def test_asr_reports_not_configured(client, auth_headers):
    r = client.post("/api/asr", headers=auth_headers,
                    files={"file": ("a.mp3", b"fake-bytes", "audio/mpeg")})
    assert r.status_code == 501
    assert "ASR" in r.json()["detail"]


# ═══════════════ 整改报告验收项 ═══════════════

def test_multi_med_in_one_sentence_binds_own_dose(client, auth_headers):
    """P0-01：一句话两种药，各自绑定自己的剂量/频次，不得互相继承。"""
    text = ("今天在市人民医院就诊，医生诊断为高血压。"
            "开苯磺酸氨氯地平片5mg每天一次，阿托伐他汀钙片20mg每晚一次，吃30天。")
    data = client.post("/api/records/extract", headers=auth_headers, json={"text": text}).json()
    meds = {m["name"]: m for m in data["meds"]}
    assert meds["苯磺酸氨氯地平片"]["dose"] == "5mg"
    assert meds["阿托伐他汀钙片"]["dose"] == "20mg", "第二味药不能继承第一味药的剂量"
    assert meds["阿托伐他汀钙片"]["timing"] == "睡前"
    assert data["summary"] == "高血压"
    assert "高血压" in data["conditions"]


def test_negation_not_recorded_as_current_med_or_allergy(client, auth_headers):
    """P0-02：不用吃 / 停药 / 没有过敏，不能记成当前用药或过敏史。"""
    text = "今天去了市医院，医生说不用吃阿莫西林胶囊，也没有对青霉素过敏，不用复查。"
    data = client.post("/api/records/extract", headers=auth_headers, json={"text": text}).json()
    assert data["meds"] == [], "否定的药不能进当前用药"
    assert data["discontinued"] == ["阿莫西林胶囊"], "应单独记进停药字段"
    assert data["allergy"] == "", "否认过敏不能记成过敏史"
    assert data["allergy_denied"] is True
    assert data["follow_up"] == "", "「不用复查」不是复诊时间"


def test_emergency_symptom_gets_safety_response(client, auth_headers):
    """P0-06：胸痛这类急症不能返回普通未命中。"""
    body = client.post("/api/chat/sync", headers=auth_headers, json={"question": "我胸痛怎么办？"}).json()
    assert body["refused"] is True
    assert "120" in body["answer"] or "急诊" in body["answer"]
    assert body["citations"] == [], "急症不应引用档案当依据"


def test_conditions_saved_and_answerable(client, auth_headers):
    """档案里要能存具体疾病/事件，并且能问出来。"""
    r = client.post("/api/records", headers=auth_headers, json={
        "client_id": "v_cond_001",
        "visit_at": "2026-10-01T09:00:00+08:00",
        "hospital": "市第一人民医院",
        "summary": "慢性非萎缩性胃炎",
        "transcript": "去年做过胃镜，结果是慢性非萎缩性胃炎，还做过胆囊结石手术。",
        "meds": [], "notes": [],
        "conditions": ["慢性非萎缩性胃炎", "胆囊结石", "胃镜", "手术"],
        "follow_up": "", "allergy": "", "mode": "manual",
    })
    assert r.status_code == 201
    assert set(["慢性非萎缩性胃炎", "胃镜"]) <= set(r.json()["conditions"])

    body = client.post("/api/chat/sync", headers=auth_headers,
                       json={"question": "我做过胃镜吗？"}).json()
    assert body["refused"] is False, "档案里有的事件必须能回答"
    assert body["citations"]


def test_adherence_question_is_not_misread(client, auth_headers):
    """P1-01：「今天吃药了吗」不能答成「应该吃什么药」。"""
    body = client.post("/api/chat/sync", headers=auth_headers, json={"question": "我今天吃药了吗？"}).json()
    assert body["refused"] is True
    assert "无法确认" in body["answer"]
    assert "奥美拉唑" not in body["answer"]


def test_diagnosis_question_answered(client, auth_headers):
    """P1-01：「我得了什么病」要返回已记录的诊断并注明来源。"""
    body = client.post("/api/chat/sync", headers=auth_headers, json={"question": "我得了什么病？"}).json()
    assert body["refused"] is False
    assert "胃炎" in body["answer"] or "高血压" in body["answer"]
    assert body["citations"]


def test_med_list_scoped_to_named_hospital(client, auth_headers):
    """P1-02：指定医院时，返回那一次的完整药单，而不是被「上次」截获。"""
    client.post("/api/records", headers=auth_headers, json={
        "client_id": "v_hosp_002",
        "visit_at": "2026-09-01T15:00:00+08:00",
        "hospital": "市第二人民医院",
        "summary": "急性支气管炎",
        "transcript": "开了阿奇霉素片 0.25g 每天一次 吃三天。",
        "meds": [{"name": "阿奇霉素片", "dose": "0.25g", "freq": "每天一次", "timing": "饭后", "days": "3"}],
        "notes": [], "conditions": ["急性支气管炎"], "mode": "manual",
    })
    body = client.post("/api/chat/sync", headers=auth_headers,
                       json={"question": "上次在市第二人民医院开了什么药？"}).json()
    assert body["refused"] is False
    assert "阿奇霉素" in body["answer"]
    assert body["citations"][0]["hospital"] == "市第二人民医院"


def test_patch_record_fixes_field(client, auth_headers):
    """P0-03：归档后能改错，不用删了重建。"""
    rec = _new_record(client, auth_headers, "v_patch_001").json()
    r = client.patch(f"/api/records/{rec['id']}", headers=auth_headers,
                     json={"summary": "慢性胃炎（已核对）", "follow_up": "三周后复查"})
    assert r.status_code == 200
    assert r.json()["summary"] == "慢性胃炎（已核对）"
    assert r.json()["follow_up"] == "三周后复查"
    assert r.json()["hospital"] == rec["hospital"], "没传的字段不能被清空"


def test_patch_respects_isolation(client, auth_headers, other_headers):
    rec = _new_record(client, auth_headers, "v_patch_002").json()
    r = client.patch(f"/api/records/{rec['id']}", headers=other_headers, json={"summary": "越权"})
    assert r.status_code == 404


# ═══════════════ 严格复审 P0-02 / P0-03 / P1-03 ═══════════════

def _seed_two_hospitals(client, headers):
    client.post("/api/records", headers=headers, json={
        "client_id": "v_oct_gastritis", "visit_at": "2026-10-01T09:00:00+08:00",
        "hospital": "市第一人民医院", "summary": "慢性胃炎",
        "transcript": "10月1日去了市第一人民医院，诊断慢性胃炎，开奥美拉唑肠溶胶囊 20mg 每天一次 饭前 吃14天。",
        "meds": [{"name": "奥美拉唑肠溶胶囊", "dose": "20mg", "freq": "每天一次", "timing": "饭前", "days": "14"}],
        "follow_up": "", "allergy": "青霉素过敏", "notes": [],
        "conditions": ["慢性胃炎"], "mode": "manual",
    })
    client.post("/api/records", headers=headers, json={
        "client_id": "v_nov_cold", "visit_at": "2026-11-01T09:00:00+08:00",
        "hospital": "市第二人民医院", "summary": "普通感冒",
        "transcript": "11月1日在市第二人民医院看普通感冒，开布洛芬 0.3g 每天两次 饭后 吃3天。",
        "meds": [{"name": "布洛芬", "dose": "0.3g", "freq": "每天两次", "timing": "饭后", "days": "3"}],
        "follow_up": "三天后复查", "allergy": "", "notes": [],
        "conditions": ["普通感冒"], "mode": "manual",
    })


def test_query_by_date_returns_that_visit(client, fresh_headers):
    _seed_two_hospitals(client, fresh_headers)
    body = client.post("/api/chat/sync", headers=fresh_headers,
                       json={"question": "10月1日开了什么药？"}).json()
    assert body["refused"] is False
    assert "奥美拉唑" in body["answer"]
    assert "布洛芬" not in body["answer"]
    assert body["citations"][0]["record_id"] != ""
    assert "2026-10-01" in body["citations"][0]["visit_at"]


def test_query_by_hospital_returns_that_visit(client, fresh_headers):
    _seed_two_hospitals(client, fresh_headers)
    for q in ["市第一人民医院开了什么药？", "上次在市第一人民医院开了什么药？"]:
        body = client.post("/api/chat/sync", headers=fresh_headers, json={"question": q}).json()
        assert body["refused"] is False, q
        assert "奥美拉唑" in body["answer"], q
        assert "布洛芬" not in body["answer"], q


def test_query_by_date_and_hospital_intersection(client, fresh_headers):
    _seed_two_hospitals(client, fresh_headers)
    body = client.post("/api/chat/sync", headers=fresh_headers,
                       json={"question": "2026年10月1日市第一人民医院的诊断是什么？"}).json()
    assert body["refused"] is False
    assert "慢性胃炎" in body["answer"]
    assert "普通感冒" not in body["answer"]


def test_query_no_match_does_not_fall_back_to_latest(client, fresh_headers):
    _seed_two_hospitals(client, fresh_headers)
    body = client.post("/api/chat/sync", headers=fresh_headers,
                       json={"question": "市第三人民医院开了什么药？"}).json()
    assert body["refused"] is True
    assert "没有找到" in body["answer"]
    assert body["citations"] == []

    body2 = client.post("/api/chat/sync", headers=fresh_headers,
                        json={"question": "2025年3月3日诊断是什么？"}).json()
    assert body2["refused"] is True
    assert body2["citations"] == []


def test_allergy_summary_keeps_history_when_latest_is_blank(client, fresh_headers):
    _seed_two_hospitals(client, fresh_headers)
    body = client.post("/api/chat/sync", headers=fresh_headers,
                       json={"question": "我有哪些过敏？"}).json()
    assert body["refused"] is False
    assert "青霉素过敏" in body["answer"]
    assert "2026年10月01日" in body["answer"]


def test_extract_visit_hint_and_drug_without_suffix(client, auth_headers):
    text = "10月1日去了市第一人民医院，医生诊断为胃炎。医生让每天吃两次阿莫西林 0.5g，共7天。"
    data = client.post("/api/records/extract", headers=auth_headers, json={"text": text}).json()
    assert data["hospital"] == "市第一人民医院"
    assert data["visit_hint"] and "2026-10-01" in data["visit_hint"]
    meds = {m["name"]: m for m in data["meds"]}
    assert "阿莫西林" in meds
    assert meds["阿莫西林"]["dose"] == "0.5g"
    assert meds["阿莫西林"]["days"] == "7"


# ————————————————— 账号生命周期（P1-08） —————————————————
def _login_headers(client, phone, password):
    r = client.post("/api/auth/login", json={"phone": phone, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_change_password_requires_old_password(client, fresh_headers):
    r = client.post("/api/me/password", headers=fresh_headers,
                    json={"old_password": "wrong-pass", "new_password": "newpass12345"})
    assert r.status_code == 400
    assert "当前密码" in r.json()["detail"]


def test_change_password_rotates_other_sessions(client):
    username = "改密" + uuid.uuid4().hex[:8]
    phone = "139" + f"{uuid.uuid4().int % 10**8:08d}"
    reg = client.post("/api/auth/register",
                      json={"username": username, "phone": phone, "password": "pass12345"})
    assert reg.status_code == 200, reg.text
    first = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    second = _login_headers(client, phone, "pass12345")

    r = client.post("/api/me/password", headers=first,
                    json={"old_password": "pass12345", "new_password": "newpass12345"})
    assert r.status_code == 200, r.text

    # 另一台设备被下线，当前这台仍然可用（否则用户会以为改密失败）
    assert client.get("/api/me", headers=second).status_code == 401
    assert client.get("/api/me", headers=first).status_code == 200

    # 旧密码失效，新密码可用
    assert client.post("/api/auth/login",
                       json={"phone": phone, "password": "pass12345"}).status_code == 401
    assert client.post("/api/auth/login",
                       json={"phone": phone, "password": "newpass12345"}).status_code == 200


def test_delete_account_removes_records_and_login(client):
    username = "注销" + uuid.uuid4().hex[:8]
    phone = "137" + f"{uuid.uuid4().int % 10**8:08d}"
    reg = client.post("/api/auth/register",
                      json={"username": username, "phone": phone, "password": "pass12345"})
    assert reg.status_code == 200
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    _new_record(client, headers, client_id="del_001")
    assert len(client.get("/api/records", headers=headers).json()) == 1

    # 密码错：不删除任何数据
    r = client.request("DELETE", "/api/me", headers=headers, json={"password": "wrong-pass"})
    assert r.status_code == 400
    assert len(client.get("/api/records", headers=headers).json()) == 1

    r = client.request("DELETE", "/api/me", headers=headers, json={"password": "pass12345"})
    assert r.status_code == 200, r.text
    assert r.json()["records"] == 1

    # 令牌与账号同时失效；同名可重新注册，且看不到旧数据
    assert client.get("/api/me", headers=headers).status_code == 401
    assert client.post("/api/auth/login",
                       json={"phone": phone, "password": "pass12345"}).status_code == 401

    # 同名可重新注册（用户名可重复），手机号已被注销释放，也能再用
    again = client.post("/api/auth/register",
                        json={"username": username, "phone": phone, "password": "pass12345"})
    assert again.status_code == 200
    again_headers = {"Authorization": f"Bearer {again.json()['access_token']}"}
    assert client.get("/api/records", headers=again_headers).json() == []


def test_delete_account_does_not_touch_other_users(client, fresh_headers, other_headers):
    before = len(client.get("/api/records", headers=other_headers).json())
    _new_record(client, fresh_headers, client_id="del_002")
    assert client.request("DELETE", "/api/me", headers=fresh_headers,
                          json={"password": "pass12345"}).status_code == 200
    assert len(client.get("/api/records", headers=other_headers).json()) == before


def test_med_query_skips_latest_visit_without_meds(client, fresh_headers):
    """按医院问药时，最近一次复查没开药，不能因此答「没有」。"""
    client.post("/api/records", headers=fresh_headers, json={
        "client_id": "fb_001",
        "visit_at": "2026-10-01T09:00:00+08:00",
        "hospital": "市第一人民医院",
        "summary": "慢性胃炎",
        "transcript": "开奥美拉唑 20mg 每天一次。",
        "meds": [{"name": "奥美拉唑", "dose": "20mg", "freq": "每天一次", "timing": "饭前", "days": "14"}],
        "mode": "manual",
    })
    client.post("/api/records", headers=fresh_headers, json={
        "client_id": "fb_002",
        "visit_at": "2026-11-02T09:00:00+08:00",
        "hospital": "市第一人民医院",
        "summary": "胃炎复查",
        "transcript": "复查，这次没开药。",
        "meds": [],
        "mode": "manual",
    })

    body = client.post("/api/chat/sync", headers=fresh_headers,
                       json={"question": "市第一人民医院开了什么药？"}).json()
    assert body["refused"] is False
    assert "奥美拉唑" in body["answer"]
    # 必须说明取的是更早那次，不能假装是最新记录
    assert "没有记录用药" in body["answer"]
    assert body["citations"] and body["citations"][0]["visit_at"].startswith("2026-10-01")


# ————————————————— 资料同步与模型代理 —————————————————
def test_profile_syncs_phone_and_idcard_across_sessions(client):
    username = "资料" + uuid.uuid4().hex[:8]
    phone = "136" + f"{uuid.uuid4().int % 10**8:08d}"
    reg = client.post("/api/auth/register",
                      json={"username": username, "phone": phone, "password": "pass12345"})
    h1 = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    r = client.put("/api/me/profile", headers=h1, json={
        "name": "同步测试", "idcard": "110101199001011234",
    })
    assert r.status_code == 200, r.text
    # 手机号是登录账号，资料页回显的就是注册时那个
    assert r.json()["phone"] == phone

    # 新会话 = 另一台设备
    tok = client.post("/api/auth/login",
                      json={"phone": phone, "password": "pass12345"}).json()["access_token"]
    p = client.get("/api/me/profile", headers={"Authorization": f"Bearer {tok}"}).json()
    assert p["phone"] == phone
    assert p["idcard"] == "110101199001011234"


def test_profile_rejects_bad_idcard(client, fresh_headers):
    assert client.put("/api/me/profile", headers=fresh_headers,
                      json={"idcard": "abc"}).status_code == 422


def test_bind_phone_rejects_bad_format_and_wrong_password(client, fresh_headers):
    assert client.post("/api/me/phone", headers=fresh_headers,
                       json={"phone": "123", "password": "pass12345"}).status_code == 422
    assert client.post("/api/me/phone", headers=fresh_headers,
                       json={"phone": "13500135000", "password": "wrong-pass"}).status_code == 400


def test_ai_requires_login(client):
    assert client.get("/api/ai/status").status_code == 401


def test_ai_status_closed_when_not_configured(client, fresh_headers):
    body = client.get("/api/ai/status", headers=fresh_headers).json()
    assert body["enabled"] is False


# ————————————————— 医生嘱咐 / 语音转写校验 —————————————————
def test_doctor_question_answers_with_record_content(client, fresh_headers):
    _new_record(client, fresh_headers, client_id="doc_001")
    r = client.post("/api/chat/sync", headers=fresh_headers, json={"question": "医生说了什么？"})
    assert r.status_code == 200, r.text
    answer = r.json()["answer"]
    assert "慢性胃炎伴糜烂" in answer
    assert "奥美拉唑" in answer
    assert "两周后来复诊" in answer
    assert r.json()["citations"]


def test_asr_requires_login(client):
    assert client.post("/api/ai/asr", json={"audio": "data:audio/webm;base64,AAA"}).status_code == 401


def test_asr_rejects_bad_payload(client, fresh_headers):
    assert client.post("/api/ai/asr", headers=fresh_headers,
                       json={"audio": "hello"}).status_code == 400
    big = "data:audio/webm;base64," + "A" * 12_000_001
    assert client.post("/api/ai/asr", headers=fresh_headers,
                       json={"audio": big}).status_code == 413


# ————————————————— 跨全部档案作答 —————————————————
def _seed_two_visits(client, headers):
    client.post("/api/records", headers=headers, json={
        "client_id": "cross_old", "visit_at": "2026-09-01T09:00:00+08:00",
        "hospital": "市第一人民医院", "summary": "慢性胃炎",
        "transcript": "9月1日诊断慢性胃炎，开奥美拉唑20mg每天一次吃14天。",
        "meds": [{"name": "奥美拉唑", "dose": "20mg", "freq": "每天一次",
                  "timing": "饭前", "days": "14"}],
        "follow_up": "两周后复诊", "notes": ["少吃辣"], "mode": "manual",
    })
    client.post("/api/records", headers=headers, json={
        "client_id": "cross_new", "visit_at": "2026-11-01T09:00:00+08:00",
        "hospital": "市第二人民医院", "summary": "急性支气管炎",
        "transcript": "11月1日诊断急性支气管炎，开阿莫西林0.5g每天两次吃7天。",
        "meds": [{"name": "阿莫西林", "dose": "0.5g", "freq": "每天两次",
                  "timing": "饭后", "days": "7"}],
        "follow_up": "一周后复查", "notes": ["多喝水"], "mode": "manual",
    })


def test_med_history_covers_all_records(client, fresh_headers):
    _seed_two_visits(client, fresh_headers)
    body = client.post("/api/chat/sync", headers=fresh_headers,
                       json={"question": "我都有什么药？"}).json()
    assert body["refused"] is False
    assert "阿莫西林" in body["answer"] and "奥美拉唑" in body["answer"], body["answer"]
    assert len(body["citations"]) >= 2


def test_general_med_answer_mentions_older_record(client, fresh_headers):
    _seed_two_visits(client, fresh_headers)
    body = client.post("/api/chat/sync", headers=fresh_headers,
                       json={"question": "我开的什么药？"}).json()
    assert "阿莫西林" in body["answer"], body["answer"]
    assert "更早的记录里还提到" in body["answer"], body["answer"]
    assert "奥美拉唑" in body["answer"]


def test_dose_question_finds_drug_in_older_record(client, fresh_headers):
    _seed_two_visits(client, fresh_headers)
    body = client.post("/api/chat/sync", headers=fresh_headers,
                       json={"question": "奥美拉唑怎么吃？"}).json()
    assert body["refused"] is False
    assert "20mg" in body["answer"] and "每天一次" in body["answer"], body["answer"]
    assert "市第一人民医院" in body["answer"], body["answer"]


def test_diagnosis_lists_all_records(client, fresh_headers):
    _seed_two_visits(client, fresh_headers)
    body = client.post("/api/chat/sync", headers=fresh_headers,
                       json={"question": "我都有什么病？"}).json()
    assert "急性支气管炎" in body["answer"], body["answer"]
    assert "慢性胃炎" in body["answer"], body["answer"]


def test_explicit_date_does_not_leak_other_records(client, fresh_headers):
    _seed_two_visits(client, fresh_headers)
    body = client.post("/api/chat/sync", headers=fresh_headers,
                       json={"question": "9月1日开了什么药？"}).json()
    assert "奥美拉唑" in body["answer"]
    assert "阿莫西林" not in body["answer"], body["answer"]


# ————————————————— 手机号登录 / 用户名可重复 —————————————————
def _reg(client, username, phone, password="pass12345"):
    return client.post("/api/auth/register",
                       json={"username": username, "phone": phone, "password": password})


def test_duplicate_username_allowed_and_data_isolated(client):
    """两个人都叫「张三」，必须是两个账号，记录互不可见。"""
    phone_a = "133" + f"{uuid.uuid4().int % 10**8:08d}"
    phone_b = "134" + f"{uuid.uuid4().int % 10**8:08d}"
    a = _reg(client, "同名张三", phone_a)
    b = _reg(client, "同名张三", phone_b)
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text
    assert a.json()["user"]["id"] != b.json()["user"]["id"]

    ha = {"Authorization": f"Bearer {a.json()['access_token']}"}
    hb = {"Authorization": f"Bearer {b.json()['access_token']}"}
    _new_record(client, ha, client_id="dup_a")
    assert len(client.get("/api/records", headers=ha).json()) == 1
    assert client.get("/api/records", headers=hb).json() == []


def test_phone_must_be_unique(client):
    phone = "135" + f"{uuid.uuid4().int % 10**8:08d}"
    assert _reg(client, "手机号甲", phone).status_code == 200
    second = _reg(client, "手机号乙", phone)
    assert second.status_code == 409
    assert "手机号" in second.json()["detail"]


def test_register_rejects_bad_phone(client):
    assert _reg(client, "手机号格式", "1234").status_code == 422
    assert _reg(client, "手机号格式", "12800128000").status_code == 422


def test_new_account_cannot_login_by_username(client):
    """新账号身份是手机号，用户名只是显示名。"""
    phone = "132" + f"{uuid.uuid4().int % 10**8:08d}"
    assert _reg(client, "只能手机号", phone).status_code == 200
    r = client.post("/api/auth/login", json={"username": "只能手机号", "password": "pass12345"})
    assert r.status_code == 401


def test_login_is_case_insensitive_to_phone_format(client):
    phone = "131" + f"{uuid.uuid4().int % 10**8:08d}"
    assert _reg(client, "格式容错", phone).status_code == 200
    spaced = phone[:3] + " " + phone[3:7] + " " + phone[7:]
    r = client.post("/api/auth/login", json={"phone": spaced, "password": "pass12345"})
    assert r.status_code == 200, r.text


def test_legacy_account_can_login_by_username_then_bind_phone(client):
    """老账号（没有手机号）不能因为改了登录方式就被锁在门外。"""
    from app.core.security import hash_password
    from app.db import repo
    from app.db.session import SessionLocal

    username = "老账号" + uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        repo.create_user(db, username, hash_password("pass12345"))

    # 用用户名还能登进来
    r = client.post("/api/auth/login", json={"username": username, "password": "pass12345"})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert r.json()["user"]["phone_bound"] is False

    # 绑定手机号后，两种方式都能登录，且都指向同一个账号
    phone = "130" + f"{uuid.uuid4().int % 10**8:08d}"
    bound = client.post("/api/me/phone", headers=headers,
                        json={"phone": phone, "password": "pass12345"})
    assert bound.status_code == 200, bound.text
    assert bound.json()["changed"] is True

    by_phone = client.post("/api/auth/login", json={"phone": phone, "password": "pass12345"})
    assert by_phone.status_code == 200
    assert by_phone.json()["user"]["id"] == r.json()["user"]["id"]
    assert client.get("/api/me", headers=headers).json()["phone"] == phone


def test_bind_phone_rejects_number_taken_by_another_account(client, fresh_headers):
    phone = "139" + f"{uuid.uuid4().int % 10**8:08d}"
    assert _reg(client, "占号的人", phone).status_code == 200
    r = client.post("/api/me/phone", headers=fresh_headers,
                    json={"phone": phone, "password": "pass12345"})
    assert r.status_code == 409



# ————————————————— 老账号迁移：填过手机号的绑定，没填的注销 —————————————————
def _legacy_user(db, username, phone_in_profile=""):
    from app.core.security import hash_password
    from app.db import repo

    user = repo.create_user(db, username, hash_password("pass12345"))
    if phone_in_profile:
        repo.upsert_profile(db, user.id, phone=phone_in_profile)
    return user


def test_admin_legacy_requires_token(client):
    from app.core.config import settings

    saved = settings.admin_token
    try:
        settings.admin_token = ""          # 未启用 = 接口不存在
        assert client.get("/api/admin/legacy-accounts").status_code == 404
        settings.admin_token = "test-admin"
        assert client.get("/api/admin/legacy-accounts").status_code == 403
        assert client.get("/api/admin/legacy-accounts",
                          headers={"X-Admin-Token": "wrong"}).status_code == 403
    finally:
        settings.admin_token = saved


def test_legacy_migration_binds_phone_and_deletes_rest(client):
    """填过手机号的能直接登录；没填的（含号码冲突）按产品要求注销。"""
    from datetime import datetime

    from sqlalchemy import select

    from app.core.config import settings
    from app.db import repo
    from app.db.models import Record, User
    from app.db.session import SessionLocal

    saved = settings.admin_token
    settings.admin_token = "test-admin"
    admin = {"X-Admin-Token": "test-admin"}
    stamp = uuid.uuid4().hex[:6]
    phone_ok = "133" + f"{uuid.uuid4().int % 10**8:08d}"
    phone_taken = "134" + f"{uuid.uuid4().int % 10**8:08d}"

    try:
        with SessionLocal() as db:
            # 1) 资料里填过手机号 → 应绑定，记录要保留
            u_ok = _legacy_user(db, "老号有手机" + stamp, phone_ok)
            repo.create_record(db, u_ok.id, {
                "client_id": "legacy_keep_" + stamp,
                "visit_at": datetime.fromisoformat("2026-10-01T09:00:00+08:00"),
                "hospital": "市第一人民医院",
                "summary": "慢性胃炎",
                "transcript": "胃炎，开奥美拉唑",
                "meds": [],
                "follow_up": "",
                "notes": [],
                "conditions": [],
                "discontinued": [],
                "allergy": "",
                "allergy_denied": False,
                "mode": "manual",
            })
            # 2) 完全没填手机号 → 应注销
            u_none = _legacy_user(db, "老号没手机" + stamp)
            # 3) 手机号被别人占了 → 无法登录，也注销
            u_conflict = _legacy_user(db, "老号撞号" + stamp, phone_taken)
            # 4) 号码主人：正常手机号账号，不该被碰
            owner = repo.create_user(db, "号码主人" + stamp, "x", phone_taken)
            ok_id, none_id, conflict_id, owner_id = u_ok.id, u_none.id, u_conflict.id, owner.id

        preview = client.get("/api/admin/legacy-accounts", headers=admin).json()
        bind_names = [x["username"] for x in preview["to_bind"]]
        del_names = [x["username"] for x in preview["to_delete"]]
        assert "老号有手机" + stamp in bind_names
        assert "老号没手机" + stamp in del_names
        assert "老号撞号" + stamp in del_names
        assert "号码主人" + stamp not in bind_names + del_names
        assert any(x["username"] == "老号撞号" + stamp for x in preview["conflicts"])

        # 没有 confirm 不许执行
        assert client.post("/api/admin/legacy-accounts", headers=admin,
                           json={"apply": True}).status_code == 400

        res = client.post("/api/admin/legacy-accounts", headers=admin,
                          json={"confirm": "DELETE"}).json()
        assert res["ok"] is True and res["backup"]["accounts"]

        with SessionLocal() as db:
            assert db.get(User, none_id) is None
            assert db.get(User, conflict_id) is None
            assert db.get(User, owner_id) is not None
            migrated = db.get(User, ok_id)
            assert migrated is not None and migrated.phone_hash
            kept = list(db.scalars(select(Record).where(Record.user_id == ok_id)))
            assert len(kept) == 1, "绑定手机号不应影响档案"

        # 绑定后能直接用手机号登录，原密码不变，记录还在
        login = client.post("/api/auth/login", json={"phone": phone_ok, "password": "pass12345"})
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert len(client.get("/api/records", headers=headers).json()) == 1
    finally:
        settings.admin_token = saved


# ————————————————— 账号清单 / 白名单清理 / 备份恢复 —————————————————
def test_purge_and_restore_roundtrip(client):
    from app.core.config import settings
    from app.db import repo
    from app.db.models import Record, User
    from app.db.session import SessionLocal

    saved = settings.admin_token
    settings.admin_token = "test-admin"
    admin = {"X-Admin-Token": "test-admin"}
    stamp = uuid.uuid4().hex[:6]
    phone_keep = "135" + f"{uuid.uuid4().int % 10**8:08d}"
    phone_drop = "136" + f"{uuid.uuid4().int % 10**8:08d}"

    try:
        with SessionLocal() as db:
            keep = repo.create_user(db, "保留号" + stamp, "x", phone_keep)
            drop = repo.create_user(db, "清理号" + stamp, "x", phone_drop)
            keep_id, drop_id = keep.id, drop.id
            repo.create_record(db, drop_id, {
                "client_id": "purge_" + stamp,
                "visit_at": datetime.fromisoformat("2026-10-02T09:00:00+08:00"),
                "hospital": "市第二人民医院",
                "summary": "支气管炎",
                "transcript": "开了阿莫西林",
                "meds": [],
                "follow_up": "",
                "notes": [],
                "conditions": [],
                "discontinued": [],
                "allergy": "",
                "allergy_denied": False,
                "mode": "manual",
            })

        # 未确认不执行；白名单为空也不执行
        assert client.post("/api/admin/accounts/purge", headers=admin,
                           json={"keep_ids": [keep_id]}).status_code == 400
        assert client.post("/api/admin/accounts/purge", headers=admin,
                           json={"keep_ids": [], "confirm": "DELETE"}).status_code == 400

        res = client.post("/api/admin/accounts/purge", headers=admin,
                          json={"keep_ids": [keep_id], "confirm": "DELETE"}).json()
        assert res["ok"] is True
        assert any(x["username"] == "清理号" + stamp for x in res["deleted"])

        with SessionLocal() as db:
            assert db.get(User, keep_id) is not None
            assert db.get(User, drop_id) is None

        # 从备份恢复：手机号与两条数据都要回来，密码用新的
        entry = next(a for a in res["backup"]["accounts"]
                     if a["user"]["username"] == "清理号" + stamp)
        r = client.post("/api/admin/accounts/restore", headers=admin,
                        json={"account": entry, "password": "newpass12345"})
        assert r.status_code == 200, r.text
        assert r.json()["records"] == 1

        login = client.post("/api/auth/login",
                            json={"phone": phone_drop, "password": "newpass12345"})
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert len(client.get("/api/records", headers=headers).json()) == 1

        # 重复恢复要拒绝
        again = client.post("/api/admin/accounts/restore", headers=admin,
                            json={"account": entry, "password": "newpass12345"})
        assert again.status_code == 409
    finally:
        settings.admin_token = saved


def test_import_records_into_existing_account(client):
    from app.core.config import settings
    from app.db import repo
    from app.db.models import User
    from app.db.session import SessionLocal

    saved = settings.admin_token
    settings.admin_token = "test-admin"
    admin = {"X-Admin-Token": "test-admin"}
    stamp = uuid.uuid4().hex[:6]
    try:
        with SessionLocal() as db:
            target = repo.create_user(db, "并入目标" + stamp, "x", "137" + f"{uuid.uuid4().int % 10**8:08d}")
            target_id = target.id

        entry = {
            "user": {"id": "old-" + stamp, "username": "并入目标" + stamp, "display_name": "", "created_at": None},
            "profile": {},
            "records": [{
                "client_id": "imp_" + stamp, "visit_at": "2026-10-03T09:00:00+08:00",
                "hospital": "市第一人民医院", "summary": "胃炎", "transcript": "开奥美拉唑",
                "meds": [], "follow_up": "", "notes": [], "conditions": [],
                "discontinued": [], "allergy": "", "allergy_denied": False, "mode": "manual",
            }],
        }
        r = client.post("/api/admin/accounts/import-records", headers=admin,
                        json={"target_id": target_id, "account": entry})
        assert r.status_code == 200, r.text
        assert r.json()["added"] == 1

        # 再导一次：按 client_id 去重
        again = client.post("/api/admin/accounts/import-records", headers=admin,
                            json={"target_id": target_id, "account": entry}).json()
        assert again["added"] == 0 and again["skipped"] == 1

        with SessionLocal() as db:
            assert db.get(User, target_id) is not None
    finally:
        settings.admin_token = saved


def test_login_accepts_phone_typed_into_username_field(client):
    """旧页面把手机号填在「用户名」栏：服务端要当成手机号处理。"""
    phone = "138" + f"{uuid.uuid4().int % 10**8:08d}"
    assert _reg(client, "旧页面兼容", phone).status_code == 200
    r = client.post("/api/auth/login", json={"username": phone, "password": "pass12345"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["phone"] == phone
