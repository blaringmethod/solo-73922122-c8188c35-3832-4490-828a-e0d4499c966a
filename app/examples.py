"""内置示例：温度扩范围、质控码缺省。供网页一键填入编辑器。"""

TEMP_FW_V1 = {
    "type": "object",
    "properties": {
        "temp": {"type": "integer", "minimum": -10, "maximum": 50},
        "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
    },
    "required": ["temp", "site"],
    "additionalProperties": False,
}

TEMP_FW_V2 = {
    "type": "object",
    "properties": {
        "temp": {"type": "integer", "minimum": -40, "maximum": 100},
        "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
    },
    "required": ["temp", "site"],
    "additionalProperties": False,
}

TEMP_PROG_OLD = {
    "type": "object",
    "properties": {
        "temp": {"type": "integer", "minimum": -10, "maximum": 50},
        "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
    },
    "required": ["temp", "site"],
    "additionalProperties": False,
}

TEMP_PROG_NEW = {
    "type": "object",
    "properties": {
        "temp": {"type": "integer", "minimum": -40, "maximum": 100},
        "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
    },
    "required": ["temp", "site"],
    "additionalProperties": False,
}

QC_FW = {
    "type": "object",
    "properties": {
        "temp": {"type": "integer", "minimum": -40, "maximum": 100},
        "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
        "qc": {"enum": ["N", "Q", None]},
    },
    "required": ["temp", "site"],
    "additionalProperties": False,
}

QC_PROG_REQUIRE = {
    "type": "object",
    "properties": {
        "temp": {"type": "integer", "minimum": -40, "maximum": 100},
        "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
        "qc": {"enum": ["N", "Q"]},
    },
    "required": ["temp", "site", "qc"],
    "additionalProperties": False,
}

QC_PROG_OPTIONAL = {
    "type": "object",
    "properties": {
        "temp": {"type": "integer", "minimum": -40, "maximum": 100},
        "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
        "qc": {"enum": ["N", "Q", None]},
    },
    "required": ["temp", "site"],
    "additionalProperties": False,
}

ANYOF_PROG = {
    "anyOf": [
        {
            "type": "object",
            "properties": {
                "temp": {"type": "integer", "minimum": -40, "maximum": 100},
                "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
                "qc": {"enum": ["N", "Q", None]},
            },
            "required": ["temp", "site"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "enum": [0, 1, 2, 3]},
                "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
            },
            "required": ["level", "site"],
            "additionalProperties": False,
        },
    ]
}

# ---- 示例四：选列暴露复核 ----
# 安全契约：两站指标取值域完全一致，删掉站号后任何公开组合都能来自两个站。
DISCLOSE_SAFE_FW = {
    "type": "object",
    "properties": {
        "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
        "temp": {"type": "integer", "minimum": -40, "maximum": 100},
        "ph": {"type": "integer", "minimum": 0, "maximum": 14},
        "qc": {"enum": ["N", "Q", None]},
    },
    "required": ["site", "temp"],
    "additionalProperties": False,
}

# 风险契约（顶层 anyOf）：两站报文形态不同——WQ-01 发温度、WQ-02 发水位档位。
# 只公开 temp 或 level 之一即可反推站号；两字段都不选（空公开报文）则通过。
DISCLOSE_RISK_FW = {
    "anyOf": [
        {
            "type": "object",
            "properties": {
                "site": {"type": "string", "enum": ["WQ-01"]},
                "temp": {"type": "integer", "minimum": -40, "maximum": 100},
            },
            "required": ["site", "temp"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "site": {"type": "string", "enum": ["WQ-02"]},
                "level": {"type": "integer", "enum": [0, 1, 2, 3]},
            },
            "required": ["site", "level"],
            "additionalProperties": False,
        },
    ]
}

EXAMPLES = [
    {
        "title": "示例一：温度扩范围",
        "story": ("固件 v2 把温度量程从 -10~50℃ 扩到 -40~100℃。若直接发布固件 v2，"
                  "旧程序（只认 -10~50℃）会拒收，系统给出反例报文（如 temp=-40）；"
                  "应先把程序升级为 -40~100℃，再发布固件 v2。"),
        "steps": ["发布固件 v1（-10~50℃）", "发布程序 analyzer 旧版（-10~50℃）",
                  "直接发布固件 v2（-40~100℃）→ 被拒并给出反例",
                  "把 analyzer 替换为新版（-40~100℃）→ 通过",
                  "再次发布固件 v2 → 通过"],
        "snippets": [
            {"label": "固件 v1：temp -10~50℃", "target": "firmware", "doc": TEMP_FW_V1},
            {"label": "固件 v2：temp -40~100℃", "target": "firmware", "doc": TEMP_FW_V2},
            {"label": "程序 analyzer 旧版：-10~50℃", "target": "program",
             "program_id": "analyzer", "doc": TEMP_PROG_OLD},
            {"label": "程序 analyzer 新版：-40~100℃", "target": "program",
             "program_id": "analyzer", "doc": TEMP_PROG_NEW},
        ],
    },
    {
        "title": "示例二：质控码缺省",
        "story": ("新固件在质控码不可得时缺省 qc 字段（或写 null）。若程序把 qc 列为 "
                  "required 且不允许 null，缺省报文会被拒收，反例为不含 qc（或 qc=null）的报文；"
                  "程序需把 qc 改为可选并允许 null。"),
        "steps": ["发布固件：qc 可选、可取 null", "发布程序 strict-qc：qc 必填 → 被拒并给出反例",
                  "把 strict-qc 替换为 qc 可选版本 → 通过"],
        "snippets": [
            {"label": "固件：qc 可缺省/为 null", "target": "firmware", "doc": QC_FW},
            {"label": "程序 strict-qc：qc 必填（会被拒）", "target": "program",
             "program_id": "strict-qc", "doc": QC_PROG_REQUIRE},
            {"label": "程序 strict-qc：qc 可选（兼容）", "target": "program",
             "program_id": "strict-qc", "doc": QC_PROG_OPTIONAL},
        ],
    },
    {
        "title": "示例三：顶层 anyOf（多报文类型）",
        "story": ("程序可声明顶层 anyOf 以同时接受多种报文形态，例如「温度报文」或「水位档位报文」。"
                  "下面是一份接受两种形态的读取格式，可发布为第二个程序试一试。"),
        "steps": ["把 anyOf 程序发布为 multiplexer → 与已登记固件做全量交叉检查"],
        "snippets": [
            {"label": "程序 multiplexer：anyOf 两种报文", "target": "program",
             "program_id": "multiplexer", "doc": ANYOF_PROG},
        ],
    },
    {
        "title": "示例四：选列暴露复核（删站号后是否仍暴露采样站）",
        "story": ("在型号页「公开选列·站号暴露复核」选固件版本与站号字段 site，再勾选拟公开字段。"
                  "安全契约两站取值域一致，公开 temp/ph 后每种报文都能来自两个站 → 通过；"
                  "风险契约用 anyOf 让两站报文形态不同（WQ-01 发 temp、WQ-02 发 level），"
                  "勾选任一字段都会拿到只能来自唯一站号的见证报文 → 不通过，改选后可复查。"),
        "steps": [
            "发布「安全契约」固件，选站号 site、公开 temp → 判定通过",
            "发布「风险契约」固件，选站号 site、公开 temp → 不通过并给出见证报文与站号 WQ-01",
            "在风险契约上改选 level → 见证站号变为 WQ-02；全部不勾选（空公开报文）→ 通过",
        ],
        "snippets": [
            {"label": "固件：选列安全契约（两站同域）", "target": "firmware",
             "doc": DISCLOSE_SAFE_FW},
            {"label": "固件：选列风险契约（anyOf 两站异形）", "target": "firmware",
             "doc": DISCLOSE_RISK_FW},
        ],
    },
]
