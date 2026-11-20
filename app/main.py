"""契约发布网页：FastAPI 入口（HTML 页面 + JSON API）。"""
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

import service
from db import (Base, DeviceModel, FirmwareFormat, ProgramFormat, PublishLog,
                SessionLocal, engine)
from disclosure import DisclosureError
from examples import EXAMPLES
from schema_spec import parse_schema

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["tojson"] = lambda v, indent=2: json.dumps(
    v, ensure_ascii=False, indent=indent)
templates.env.filters["tojsonc"] = lambda v: json.dumps(
    v, ensure_ascii=False, separators=(",", ":"))


def _wait_for_db(retries=40, delay=1.0):
    for i in range(retries):
        try:
            with engine.connect():
                return
        except OperationalError:
            if i == retries - 1:
                raise
            time.sleep(delay)


@asynccontextmanager
async def lifespan(app):
    _wait_for_db()
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="水质平台报文契约发布", lifespan=lifespan)


# ---------------- 公共数据 ----------------

def _get_model_or_404(name: str) -> DeviceModel:
    with SessionLocal() as s:
        m = s.execute(select(DeviceModel).where(DeviceModel.name == name)).scalar_one_or_none()
        if m is None:
            raise HTTPException(404, f"型号 {name!r} 不存在")
        return DeviceModel(id=m.id, name=m.name)


def _model_context(name: str, result=None):
    with SessionLocal() as s:
        model = s.execute(select(DeviceModel).where(DeviceModel.name == name)).scalar_one_or_none()
        if model is None:
            raise HTTPException(404, f"型号 {name!r} 不存在")
        firmwares = s.execute(select(FirmwareFormat)
                              .where(FirmwareFormat.model_id == model.id)
                              .order_by(FirmwareFormat.version)).scalars().all()
        programs = s.execute(select(ProgramFormat)
                             .where(ProgramFormat.model_id == model.id)
                             .order_by(ProgramFormat.program_id)).scalars().all()
        logs = s.execute(select(PublishLog).where(PublishLog.model_id == model.id)
                         .order_by(PublishLog.id.desc()).limit(50)).scalars().all()
        s.expunge_all()
        # 每个固件版本：各分支字段并集（保持首次出现顺序），供选列页渲染勾选框
        fw_fields = []
        for f in firmwares:
            schema = parse_schema(f.doc)
            ordered = []
            seen = set()
            for b in schema.branches:
                for name in b.properties:
                    if name not in seen:
                        seen.add(name)
                        ordered.append(name)
            fw_fields.append({"version": f.version, "fields": ordered,
                              "schema_json": json.dumps(f.doc, ensure_ascii=False)})
        return {
            "model": model, "firmwares": firmwares, "programs": programs,
            "logs": logs, "examples": EXAMPLES, "result": result,
            "max_formats": service.MAX_FORMATS,
            "fw_fields": fw_fields, "review": None, "review_error": None,
            "review_form": None,
        }


# ---------------- HTML 页面 ----------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with SessionLocal() as s:
        models = s.execute(select(DeviceModel).order_by(DeviceModel.name)).scalars().all()
        rows = []
        for m in models:
            fw = s.execute(select(FirmwareFormat).where(FirmwareFormat.model_id == m.id)).scalars().all()
            pg = s.execute(select(ProgramFormat).where(ProgramFormat.model_id == m.id)).scalars().all()
            rows.append({"name": m.name, "fw_count": len(fw), "pg_count": len(pg)})
    return templates.TemplateResponse(request, "index.html", {"rows": rows})


@app.post("/models")
def create_model(request: Request, name: str = Form(...)):
    name = (name or "").strip()
    if not service.valid_name(name):
        with SessionLocal() as s:
            models = s.execute(select(DeviceModel).order_by(DeviceModel.name)).scalars().all()
            rows = [{"name": m.name, "fw_count": 0, "pg_count": 0} for m in models]
        return templates.TemplateResponse(
            request, "index.html", {"rows": rows,
                                    "error": "型号名只能由字母数字及 _ . - 组成，长度 1~64"},
            status_code=400)
    with SessionLocal() as s:
        if s.execute(select(DeviceModel).where(DeviceModel.name == name)).scalar_one_or_none():
            return RedirectResponse(f"/models/{name}", status_code=303)
        s.add(DeviceModel(name=name))
        s.commit()
    return RedirectResponse(f"/models/{name}", status_code=303)


@app.get("/models/{name}", response_class=HTMLResponse)
def model_page(request: Request, name: str):
    return templates.TemplateResponse(request, "model.html", _model_context(name))


@app.post("/models/{name}/firmware", response_class=HTMLResponse)
def fw_publish_html(request: Request, name: str, schema_text: str = Form(...)):
    model = _get_model_or_404(name)
    try:
        doc = json.loads(schema_text)
    except json.JSONDecodeError as e:
        outcome = service.PublishOutcome(False, f"JSON 解析失败：{e}")
    else:
        outcome = service.publish_firmware(model.id, doc)
    return templates.TemplateResponse(request, "model.html",
                                      _model_context(name, result=outcome))


@app.post("/models/{name}/programs", response_class=HTMLResponse)
def prog_publish_html(request: Request, name: str,
                      program_id: str = Form(...), schema_text: str = Form(...)):
    model = _get_model_or_404(name)
    try:
        doc = json.loads(schema_text)
    except json.JSONDecodeError as e:
        outcome = service.PublishOutcome(False, f"JSON 解析失败：{e}")
    else:
        outcome = service.publish_program(model.id, program_id.strip(), doc)
    return templates.TemplateResponse(request, "model.html",
                                      _model_context(name, result=outcome))


@app.post("/models/{name}/disclosure", response_class=HTMLResponse)
def disclosure_html(request: Request, name: str, version: str = Form(...),
                    site_field: str = Form(...),
                    public_fields: list[str] = Form(default=[])):
    model = _get_model_or_404(name)
    form = {"version": version, "site_field": (site_field or "").strip(),
            "public_fields": list(public_fields)}
    ctx = _model_context(name)
    ctx["review_form"] = form
    try:
        review = service.review_disclosure(
            model.id, version, (site_field or "").strip(), list(public_fields))
    except DisclosureError as e:
        ctx["review_error"] = str(e)
    else:
        ctx["review"] = review
    return templates.TemplateResponse(request, "model.html", ctx)


# ---------------- JSON API ----------------

class ModelIn(BaseModel):
    name: str


class FirmwareIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    doc: dict = Field(alias="schema")


class ProgramIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    program_id: str
    doc: dict = Field(alias="schema")


class DisclosureIn(BaseModel):
    version: int
    site_field: str
    public_fields: list[str] = []


def _outcome_response(outcome: service.PublishOutcome, extra=None):
    body = {"ok": outcome.ok, "message": outcome.message}
    if outcome.problems:
        body["problems"] = outcome.problems
    if extra:
        body.update(extra)
    status = 200 if outcome.ok else (409 if outcome.problems else 422)
    return JSONResponse(body, status_code=status)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/models", status_code=201)
def api_create_model(body: ModelIn):
    name = body.name.strip()
    if not service.valid_name(name):
        raise HTTPException(422, "型号名只能由字母数字及 _ . - 组成，长度 1~64")
    with SessionLocal() as s:
        if s.execute(select(DeviceModel).where(DeviceModel.name == name)).scalar_one_or_none():
            raise HTTPException(409, f"型号 {name!r} 已存在")
        s.add(DeviceModel(name=name))
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            raise HTTPException(409, f"型号 {name!r} 已存在")
    return {"ok": True, "name": name}


@app.get("/api/models/{name}")
def api_model_state(name: str):
    ctx = _model_context(name)
    return {
        "name": ctx["model"].name,
        "firmwares": [{"version": f.version, "schema": f.doc,
                       "created_at": str(f.created_at)} for f in ctx["firmwares"]],
        "programs": [{"program_id": p.program_id, "schema": p.doc,
                      "updated_at": str(p.updated_at)} for p in ctx["programs"]],
    }


@app.get("/api/models/{name}/log")
def api_model_log(name: str):
    ctx = _model_context(name)
    return {"logs": [{"action": l.action, "program_id": l.program_id,
                      "result": l.result, "detail": l.detail,
                      "created_at": str(l.created_at)} for l in ctx["logs"]]}


@app.post("/api/models/{name}/firmware")
def api_publish_firmware(name: str, body: FirmwareIn):
    model = _get_model_or_404(name)
    outcome = service.publish_firmware(model.id, body.doc)
    return _outcome_response(outcome, {"version": outcome.version})


@app.post("/api/models/{name}/programs")
def api_publish_program(name: str, body: ProgramIn):
    model = _get_model_or_404(name)
    outcome = service.publish_program(model.id, body.program_id.strip(), body.doc)
    return _outcome_response(outcome)


@app.post("/api/models/{name}/disclosure")
def api_disclosure(name: str, body: DisclosureIn):
    """选列暴露复核。

    status=pass 通过；status=risk 给见证公开报文与唯一站号；
    status=unknown 为待核（求解器未能完成判定）；请求不合法返回 422。
    """
    model = _get_model_or_404(name)
    try:
        review = service.review_disclosure(
            model.id, body.version, body.site_field.strip(), body.public_fields)
    except DisclosureError as e:
        return JSONResponse({"ok": False, "status": "invalid", "message": str(e)},
                            status_code=422)
    return {
        "ok": True,
        "status": review.status,
        "message": review.message,
        "firmware_version": review.firmware_version,
        "site_field": review.site_field,
        "public_fields": review.public_fields,
        "stations": review.stations,
        "witness": review.witness,
        "witness_site": review.witness_site,
    }
