"""选列暴露复核（删站号后，剩余公开字段是否仍能反推出采样站）。

背景：对外分享水质明细前先删掉站号字段，但剩余字段的取值组合仍可能把某条
公开报文唯一锁定到一个采样站。本模块在固件「写入契约」允许的**全部合法报文**
空间上做全量判定（z3 逻辑证明，非抽样）：

- 按固件的某个已发布版本选一个站号字段、再勾选若干拟公开字段；
- 公开报文 = 合法原报文裁剪到公开字段（缺省字段不出现、显式 null 保留为 null）；
- 仅当**每一种可能的公开报文**都能由至少两个**不同站号**的合法原报文裁剪得到，
  才判通过；否则给出一条只能由唯一站号产生的公开报文及该站号，便于改选复查；
- 求解器无法完成判定（超时/unknown）时返回「待核」，不给出通过结论。

判定可判定性依据（受限语法下字段相互独立，字段合法性只取决于字段自身）：
公开报文 P 能由站号 s 的合法原报文裁剪得到，等价于存在固件的某分支 b：
b 含全部公开字段、P 的每个取值满足 b 中该字段的类型约束、且 b 的其余必填字段
都能补出合法值（总是成立：必填站号可填 s，其它字段按其域任取一合法值）。
因此用一次无量词 SAT 查询即可覆盖全部公开报文，无需遍历。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from z3 import And, BoolVal, Implies, Not, Or, Solver

import schema_spec
from schema_spec import Schema
from solver import SOLVER_TIMEOUT_MS, _Encoder, decode_message

MAX_STATIONS = 8  # 站号字段各分支字符串枚举的并集至多 8 个站号


class DisclosureError(ValueError):
    """选列复核请求不合法（字段选择/站号字段不符合约束）。"""


@dataclass
class DisclosureResult:
    status: str                 # pass | risk | unknown
    message: str
    firmware_version: Optional[int] = None
    site_field: Optional[str] = None
    public_fields: list = field(default_factory=list)
    stations: list = field(default_factory=list)
    witness: Optional[dict] = None       # 只能由唯一站号产生的公开报文
    witness_site: Optional[str] = None   # 对应的唯一站号


# ---------------------------------------------------------------- 请求校验

def _validate_request(schema: Schema, site_field: str, public_fields: list):
    if not isinstance(site_field, str) or not site_field:
        raise DisclosureError("必须指定站号字段名")
    if not isinstance(public_fields, list) or not all(isinstance(f, str) for f in public_fields):
        raise DisclosureError("公开字段必须是字符串数组")
    if len(set(public_fields)) != len(public_fields):
        raise DisclosureError("公开字段存在重复")
    if any(not f for f in public_fields):
        raise DisclosureError("公开字段名不能为空")
    if site_field in public_fields:
        raise DisclosureError("站号字段不能同时选为公开字段")

    all_fields = set()
    stations = set()
    for i, branch in enumerate(schema.branches):
        props = branch.properties
        all_fields |= set(props)
        if site_field not in props:
            raise DisclosureError(f"站号字段 {site_field!r} 未在 anyOf 分支 {i} 中声明，"
                                  "要求各分支均声明该字段")
        if site_field not in branch.required:
            raise DisclosureError(f"站号字段 {site_field!r} 在 anyOf 分支 {i} 中不是必填，"
                                  "要求各分支均必填")
        spec = props[site_field]
        if spec.allow_null or spec.int_kind != "none" or not spec.str_set:
            raise DisclosureError(
                f"站号字段 {site_field!r} 在 anyOf 分支 {i} 中必须是纯字符串枚举"
                "（不得允许 null 或整数）")
        stations |= set(spec.str_set)

    if site_field not in all_fields:  # 理论上不可达，防御
        raise DisclosureError(f"站号字段 {site_field!r} 不存在")
    if len(stations) > MAX_STATIONS:
        raise DisclosureError(
            f"站号字段各分支字符串枚举的并集为 {len(stations)} 个站号，"
            f"超过 {MAX_STATIONS} 个上限：{sorted(stations)}")
    unknown = [f for f in public_fields if f not in all_fields]
    if unknown:
        raise DisclosureError(f"公开字段在固件契约中不存在：{unknown}")
    return sorted(stations)


# ---------------------------------------------------------------- z3 编码

def _branch_accepts_projection(branch, enc: _Encoder, public_fields: list):
    """z3 约束：该分支接受当前候选公开报文（其余必填字段默认可补出合法值）。"""
    props = branch.properties
    cons = []
    for f in public_fields:
        p, k, v, s = enc.present[f], enc.kind[f], enc.ival[f], enc.sval[f]
        spec = props.get(f)
        if spec is None:
            # 分支未声明该字段而它出现 -> additionalProperties:false 拒收
            cons.append(Not(p))
        elif f in branch.required:
            # 必填的公开字段必须出现且取值合法
            cons.append(And(p, enc.field_ok(f, spec)))
        else:
            cons.append(Implies(p, enc.field_ok(f, spec)))
    return And(*cons) if cons else BoolVal(True)


def check_disclosure(schema: Schema, site_field: str,
                     public_fields: list) -> DisclosureResult:
    """对固件写入契约做选列暴露全量判定。"""
    stations = _validate_request(schema, site_field, public_fields)

    # 参与编码的字段：站号 + 全部公开字段（站号用于枚举站号、构造唯一性约束）
    fields = sorted(set(public_fields) | {site_field})
    str_tables = {}
    for f in fields:
        strs = set()
        for b in schema.branches:
            spec = b.properties.get(f)
            if spec and spec.str_set:
                strs |= set(spec.str_set)
        if strs:
            str_tables[f] = sorted(strs)

    enc = _Encoder(fields, str_tables)
    solver = Solver()
    solver.set("timeout", SOLVER_TIMEOUT_MS)
    solver.add(enc.wellformed())
    # 候选：一组合法原报文编码变量（站号 + 公开字段都在同一报文里）
    solver.add(enc.schema_ok(schema))

    # 每个站号 s：是否存在一个「站号 = s 且公开部分与候选完全一致」的合法原报文。
    # 注意备选原报文的站号是独立的存在量词，不能约束候选自己的站号变量；
    # 字段相互独立，存在性等价于某分支接受候选的公开投影、且该分支站号枚举含 s。
    feasible = []
    for s in stations:
        per_branch = []
        for b in schema.branches:
            if s not in b.properties[site_field].str_set:
                continue  # 该分支根本不发站号 s
            per_branch.append(
                _branch_accepts_projection(b, enc, list(public_fields)))
        feasible.append(Or(*per_branch) if per_branch else BoolVal(False))

    # 候选公开报文只能由恰好一个站号产生 => 暴露
    exactly_one = [And(fs, *[Not(g) for j, g in enumerate(feasible) if j != i])
                   for i, fs in enumerate(feasible)]
    solver.add(Or(*exactly_one))
    # 优先要求见证含至少一个公开字段（空投影见证只在结构差异导致时作为最后手段）
    if public_fields:
        solver.add(Or(*[enc.present[f] for f in public_fields]))

    result = solver.check()
    if str(result) == "unknown":
        return DisclosureResult(
            "unknown", "z3 求解超时/未知，未能完成全量判定，结论待核（不得视为通过）",
            site_field=site_field, public_fields=list(public_fields), stations=stations)
    if str(result) != "sat":
        return DisclosureResult(
            "pass",
            f"全量判定通过：每一种可能的公开报文都能由至少两个不同站号的合法原报文裁剪得到"
            f"（共 {len(stations)} 个站号：{', '.join(stations)}）",
            site_field=site_field, public_fields=list(public_fields), stations=stations)

    model = solver.model()
    full = decode_message(model, enc, fields, str_tables)
    # 公开报文：裁剪到站号之外的公开字段（缺省与显式 null 自然保留区别）
    witness = {f: full[f] for f in public_fields if f in full}
    py_sites = achievable_sites(schema, site_field, witness, public_fields)
    if len(py_sites) != 1:
        # 纯 Python 独立复核失败：不输出未经证实的结论，转待核
        return DisclosureResult(
            "unknown", "求解器结论未通过独立复核，未能完成判定，结论待核",
            site_field=site_field, public_fields=list(public_fields), stations=stations)
    witness_site = next(iter(py_sites))
    # 再补一条完整合法原报文做复核（站号必须就是该唯一站号）
    original = complete_message(schema, site_field, witness, witness_site, public_fields)
    if original is None or not schema_spec.message_valid(original, schema) \
            or original.get(site_field) != witness_site \
            or {f: original[f] for f in public_fields if f in original} != witness:
        return DisclosureResult(
            "unknown", "见证报文复核失败，未能完成判定，结论待核",
            site_field=site_field, public_fields=list(public_fields), stations=stations)
    return DisclosureResult(
        "risk",
        "存在只能由唯一站号产生的公开报文：删掉站号后该报文仍会暴露采样站，请改选字段后复查",
        site_field=site_field, public_fields=list(public_fields), stations=stations,
        witness=witness, witness_site=witness_site)


# ------------------------------------------------- 纯 Python 独立复核

def _branch_accepts_msg(branch, projection: dict, public_fields: set) -> bool:
    props = branch.properties
    for f, val in projection.items():
        spec = props.get(f)
        if spec is None:
            return False  # additionalProperties: false
        if not schema_spec._field_valid(val, spec):
            return False
    for req in branch.required:
        if req in projection:
            continue  # 已校验取值合法
        if req in public_fields:
            # 必填的公开字段在投影中缺省：补值会改变公开报文，该分支不接受此投影
            return False
        # 其余必填字段（站号及其它非公开字段）可在不改变投影的前提下补合法值
    return True


def achievable_sites(schema: Schema, site_field: str, projection: dict,
                     public_fields=None) -> set:
    """纯 Python 判定：公开报文 projection 能由哪些站号的合法原报文裁剪得到。"""
    public_fields = set(projection) if public_fields is None else set(public_fields)
    sites = set()
    for b in schema.branches:
        if not _branch_accepts_msg(b, projection, public_fields):
            continue
        sites |= set(b.properties[site_field].str_set)
    return sites


def _sample_value(spec: schema_spec.FieldSpec):
    if spec.allow_null:
        return None
    if spec.str_set:
        return next(iter(spec.str_set))
    if spec.int_kind == "set":
        return next(iter(spec.int_set))
    if spec.int_kind == "range":
        if spec.int_lo is not None:
            return spec.int_lo
        if spec.int_hi is not None:
            return spec.int_hi
        return 0
    raise DisclosureError("字段无可取的合法值")  # 受限语法下不可达


def complete_message(schema: Schema, site_field: str, projection: dict, station: str,
                     public_fields=None):
    """在给定站号下，把公开报文补成一条完整的合法原报文（用于见证复核）。"""
    public_fields = set(projection) if public_fields is None else set(public_fields)
    for b in schema.branches:
        if station not in b.properties[site_field].str_set:
            continue
        if not _branch_accepts_msg(b, projection, public_fields):
            continue
        msg = dict(projection)
        for fname, spec in b.properties.items():
            if fname in msg:
                continue
            msg[fname] = station if fname == site_field else _sample_value(spec)
        if schema_spec.message_valid(msg, schema):
            return msg
    return None
