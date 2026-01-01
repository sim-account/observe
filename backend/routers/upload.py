import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import io
import pandas as pd
from fastapi import APIRouter, HTTPException, UploadFile, File
from typing import Optional

import session_store
from models import UploadResponse
from src.event_log_parser import detect_columns
from src.case_attribute_extractor import detect_case_id_column

router = APIRouter()


def _parse_upload(content: bytes, fname: str) -> pd.DataFrame:
    if fname.endswith(".csv"):
        return pd.read_csv(io.BytesIO(content))
    elif fname.endswith((".xls", ".xlsx")):
        return pd.read_excel(io.BytesIO(content))
    try:
        return pd.read_csv(io.BytesIO(content))
    except Exception:
        return pd.read_excel(io.BytesIO(content))


@router.post("/upload", response_model=UploadResponse)
async def upload_event_log(file: UploadFile = File(...)):
    content = await file.read()
    fname = file.filename or ""
    try:
        df = _parse_upload(content, fname)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse file: {e}")

    if df.empty:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    detected = detect_columns(df)
    session_id = str(uuid.uuid4())
    session_store.create(session_id)
    session_store.update(session_id, raw_df=df.to_json(orient="split", date_format="iso"))

    return UploadResponse(
        session_id=session_id,
        row_count=len(df),
        columns=df.columns.tolist(),
        detected_mapping={k: v for k, v in detected.items()},
    )


@router.post("/session/{session_id}/upload-case-log")
async def upload_case_log(session_id: str, file: UploadFile = File(...)):
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    content = await file.read()
    fname = file.filename or ""
    try:
        df = _parse_upload(content, fname)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse case log: {e}")

    if df.empty:
        raise HTTPException(status_code=400, detail="Case log is empty")

    # Try to infer the case ID column from the event log column mapping hint
    observations = session.get("discovered_model")
    hint = ""
    if observations:
        hint = observations.get("case_id_col", "")

    detected_case_id = detect_case_id_column(df, hint)

    session_store.update(
        session_id,
        case_log_raw=df.to_json(orient="split", date_format="iso"),
        case_log_case_id_col=detected_case_id,
    )

    return {
        "columns": df.columns.tolist(),
        "detected_case_id": detected_case_id,
        "row_count": len(df),
    }

