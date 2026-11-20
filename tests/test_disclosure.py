"""单元自测：选列暴露复核（disclosure.check_disclosure）。

覆盖：
- 站号字段校验（各分支必填、纯字符串枚举、并集 ≤8）
- 通过 / 暴露风险 / 见证报文与站号 / 待核
- 缺省与显式 null 的区别
- 顶层 anyOf 按全部合法报文判定
运行：PYTHONPATH=<依赖目录> python3 tests/test_disclosure.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import disclosure  # noqa: E402
from disclosure import (DisclosureError, achievable_sites, check_disclosure,  # noqa: E402
                        complete_message)
from schema_spec import message_valid, parse_schema  # noqa: E402

PASS = []


def ok(name, cond):
    assert cond, f"FAILED: {name}"
    PASS.append(name)


def expect_error(name, schema, site, fields):
    try:
        check_disclosure(schema, site, fields)
    except DisclosureError:
        PASS.append(name)
        return
    raise AssertionError(f"FAILED(应拒绝但未拒绝): {name}")


def obj(props, required, site_enum=None):
    p = dict(props)
    if site_enum is not None:
        p["site"] = {"type": "string", "enum": site_enum}
    req = list(required)
    if "site" not in req:
        req = ["site"] + req
    return {"type": "object", "properties": p, "required": req,
            "additionalProperties": False}


# ---------- 安全契约：两站同域 ----------
safe = parse_schema(obj(
    {"temp": {"type": "integer", "minimum": -40, "maximum": 100},
     "ph": {"type": "integer", "minimum": 0, "maximum": 14},
     "qc": {"enum": ["N", "Q", None]}},
    required=["temp"], site_enum=["WQ-01", "WQ-02"]))

r = check_disclosure(safe, "site", ["temp"])
ok("安全:公开temp通过", r.status == "pass")
r = check_disclosure(safe, "site", ["temp", "ph", "qc"])
ok("安全:公开全部非站字段通过", r.status == "pass" and r.witness is None)
r = check_disclosure(safe, "site", [])
ok("安全:空公开字段通过", r.status == "pass")

# ---------- 风险契约：anyOf 两站异形 ----------
risk = parse_schema({"anyOf": [
    obj({"temp": {"type": "integer", "minimum": -40, "maximum": 100}},
        required=["temp"], site_enum=["WQ-01"]),
    obj({"level": {"type": "integer", "enum": [0, 1, 2, 3]}},
        required=["level"], site_enum=["WQ-02"]),
]})

r = check_disclosure(risk, "site", ["temp"])
ok("风险:公开temp不通过", r.status == "risk" and r.witness_site == "WQ-01"
   and set(r.witness) == {"temp"} and isinstance(r.witness["temp"], int))
ok("风险:见证是合法公开形态", message_valid(
    {"site": "WQ-01", "temp": r.witness["temp"]}, risk))
r2 = check_disclosure(risk, "site", ["level"])
ok("风险:公开level不通过且站号WQ-02", r2.status == "risk" and r2.witness_site == "WQ-02")
r3 = check_disclosure(risk, "site", [])
ok("风险:空公开报文通过(两站都能产生空投影)", r3.status == "pass")

# 纯 Python 复核函数
ok("复核:temp投影只WQ-01可达",
   achievable_sites(risk, "site", {"temp": 20}, ["temp"]) == {"WQ-01"})
ok("复核:空投影两站可达", achievable_sites(risk, "site", {}, []) == {"WQ-01", "WQ-02"})
filled = complete_message(risk, "site", {"temp": 20}, "WQ-01", ["temp"])
ok("复核:补全为合法原报文", filled is not None and message_valid(filled, risk)
   and filled["site"] == "WQ-01" and filled["temp"] == 20)

# ---------- 取值域不同导致的暴露（单分支） ----------
domain_risk = parse_schema(obj(
    {"temp": {"type": "integer", "minimum": 0, "maximum": 100}},
    required=["temp"], site_enum=["WQ-01", "WQ-02"]))
# WQ-01 只在 0~40，WQ-02 只在 60~100 —— 用 anyOf 表达站点差异化取值域
domain_risk = parse_schema({"anyOf": [
    {"type": "object",
     "properties": {"site": {"type": "string", "enum": ["WQ-01"]},
                    "temp": {"type": "integer", "minimum": 0, "maximum": 40}},
     "required": ["site", "temp"], "additionalProperties": False},
    {"type": "object",
     "properties": {"site": {"type": "string", "enum": ["WQ-02"]},
                    "temp": {"type": "integer", "minimum": 60, "maximum": 100}},
     "required": ["site", "temp"], "additionalProperties": False},
]})
r = check_disclosure(domain_risk, "site", ["temp"])
ok("值域差异:暴露且见证temp在唯一站域内",
   r.status == "risk" and r.witness_site in ("WQ-01", "WQ-02")
   and ((r.witness_site == "WQ-01" and 0 <= r.witness["temp"] <= 40)
        or (r.witness_site == "WQ-02" and 60 <= r.witness["temp"] <= 100)))

# ---------- 缺省 vs 显式 null 的区别 ----------
null_schema = parse_schema(obj(
    {"qc": {"enum": ["N", None]}}, required=[], site_enum=["WQ-01", "WQ-02"]))
# 两站同域 -> 无论缺省还是 null 都两站可达，应通过
ok("null:缺省/null均不暴露", check_disclosure(null_schema, "site", ["qc"]).status == "pass")
# 差异化：分支1允许 qc 缺省或 null，分支2只允许 qc 缺省（无该字段声明之外的形态用分支区分）
null_risk = parse_schema({"anyOf": [
    {"type": "object",
     "properties": {"site": {"type": "string", "enum": ["WQ-01"]},
                    "qc": {"enum": ["N", None]}},
     "required": ["site"], "additionalProperties": False},
    {"type": "object",
     "properties": {"site": {"type": "string", "enum": ["WQ-02"]},
                    "qc": {"enum": ["N"]}},
     "required": ["site"], "additionalProperties": False},
]})
r = check_disclosure(null_risk, "site", ["qc"])
ok("null:显式null只WQ-01可达(缺省仍两站可达,但存在唯一站报文即不通过)",
   r.status == "risk" and r.witness == {"qc": None} and r.witness_site == "WQ-01")
ok("null:缺省投影仍两站可达",
   achievable_sites(null_risk, "site", {}, ["qc"]) == {"WQ-01", "WQ-02"})
ok("null:显式null仅WQ-01",
   achievable_sites(null_risk, "site", {"qc": None}, ["qc"]) == {"WQ-01"})

# ---------- 站号字段校验 ----------
expect_error("校验:站号字段不存在", safe, "nope", ["temp"])
expect_error("校验:站号与公开字段重叠", safe, "site", ["site", "temp"])
expect_error("校验:公开字段不存在", safe, "site", ["nope"])
expect_error("校验:公开字段重复", safe, "site", ["temp", "temp"])
expect_error("校验:站号允许null被拒",
             parse_schema(
             {"type": "object",
              "properties": {"site": {"enum": ["WQ-01", "WQ-02", None]},
                             "temp": {"type": "integer"}},
              "required": ["site", "temp"], "additionalProperties": False}),
             "site", ["temp"])
expect_error("校验:站号为整数枚举被拒",
             parse_schema(
             {"type": "object",
              "properties": {"site": {"type": "integer", "enum": [1, 2]},
                             "temp": {"type": "integer"}},
              "required": ["site", "temp"], "additionalProperties": False}),
             "site", ["temp"])
expect_error("校验:站号非必填被拒",
             parse_schema(
             {"type": "object",
              "properties": {"site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
                             "temp": {"type": "integer"}},
              "required": ["temp"], "additionalProperties": False}),
             "site", ["temp"])
expect_error("校验:某分支缺站号被拒",
             parse_schema({"anyOf": [
                 {"type": "object",
                  "properties": {"site": {"type": "string", "enum": ["WQ-01"]},
                                 "temp": {"type": "integer"}},
                  "required": ["site", "temp"], "additionalProperties": False},
                 {"type": "object",
                  "properties": {"temp": {"type": "integer"}},
                  "required": ["temp"], "additionalProperties": False},
             ]}),
             "site", ["temp"])

# ---------- 站号并集上限 8 ----------
nine = [f"S{i}" for i in range(9)]
expect_error("校验:并集9站超上限",
             parse_schema(obj({"temp": {"type": "integer"}}, required=["temp"],
                              site_enum=nine)),
             "site", ["temp"])
eight = [f"S{i}" for i in range(8)]
ok("校验:并集8站允许", check_disclosure(
    parse_schema(obj({"temp": {"type": "integer"}}, required=["temp"], site_enum=eight)),
    "site", ["temp"]).status == "pass")

# ---------- 待核：模拟求解器 unknown ----------
class _FakeSolver:
    def set(self, *a, **k): pass
    def add(self, *a, **k): pass
    def check(self): return "unknown"


orig_solver = disclosure.Solver
disclosure.Solver = lambda *a, **k: _FakeSolver()
try:
    r = check_disclosure(safe, "site", ["temp"])
    ok("待核:unknown映射", r.status == "unknown" and "待核" in r.message and r.witness is None)
finally:
    disclosure.Solver = orig_solver

print(f"全部 {len(PASS)} 项选列复核单元自测通过")
