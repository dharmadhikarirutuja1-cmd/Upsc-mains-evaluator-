<Plan>
**Backend** (`/app/backend/server.py`):
- Models: `User`, `Session`, `Evaluation`
- Auth: POST `/api/auth/session`, GET `/api/auth/me`, POST `/api/auth/logout`
- Helper: `get_current_user` (reads session_token from cookie OR Authorization header)
- Evaluations: POST `/api/evaluations` (multipart: file + question_reference), GET `/api/evaluations`, GET `/api/evaluations/{id}`
- Dashboard: GET `/api/dashboard/stats`
- Rubric: GET `/api/rubric`
- Gemini: use `emergentintegrations.LlmChat` with model `gemini-3.1-pro-preview`, two-step: OCR extraction → evaluation (JSON)
- PDF: use `pypdfium2` to render each page to PNG, send images to Gemini

**Frontend** (`/app/frontend/src/`):
- `App.js` with routes: `/` (Login), `/dashboard`, `/new`, `/history`, `/rubric`, `/report/:id`, with session_id fragment handling
- `contexts/AuthContext.jsx`
- `components/Sidebar.jsx`, `components/Layout.jsx`, `components/ScoreBadge.jsx`, `components/ScanningAnimation.jsx`
- Pages: `Login`, `Dashboard`, `NewEvaluation`, `History`, `Rubric`, `Report`
- Styling: Work Sans + IBM Plex Sans, UPSC Blue `#003366`, Gold `#D4AF37`

Now creating all files in parallel.
Action: file_editor create /app/backend/server.py --file-text "import os
import io
import uuid
import logging
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import FastAPI, APIRouter, Request, Response, HTTPException, Depends, UploadFile, File, Form, Cookie
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
import httpx
import pypdfium2 as pdfium
from PIL import Image

from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / \".env\")

MONGO_URL = os.environ[\"MONGO_URL\"]
DB_NAME = os.environ[\"DB_NAME\"]
EMERGENT_LLM_KEY = os.environ.get(\"EMERGENT_LLM_KEY\", \"\")
GEMINI_MODEL = \"gemini-3.1-pro-preview\"

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

app = FastAPI()
api_router = APIRouter(prefix=\"/api\")

logging.basicConfig(level=logging.INFO, format=\"%(asctime)s - %(levelname)s - %(message)s\")
logger = logging.getLogger(__name__)


# ------------------ Models ------------------
class User(BaseModel):
    user_id: str
    email: str
    name: str
    picture: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ScoreBreakdown(BaseModel):
    content_accuracy: float
    structure: float
    map_diagram: float


class Evaluation(BaseModel):
    id: str
    user_id: str
    question_reference: str
    filename: str
    file_type: str
    pages: int
    transcription: str
    overall_score: float
    breakdown: ScoreBreakdown
    strengths: List[str]
    improvements: List[str]
    detailed_feedback: str
    created_at: datetime


# ------------------ Auth Helpers ------------------
async def get_current_user(request: Request) -> User:
    # Prefer cookie, fallback to Authorization header
    session_token = request.cookies.get(\"session_token\")
    if not session_token:
        auth = request.headers.get(\"Authorization\", \"\")
        if auth.startswith(\"Bearer \"):
            session_token = auth.split(\" \", 1)[1].strip()
    if not session_token:
        raise HTTPException(status_code=401, detail=\"Not authenticated\")

    sess = await db.user_sessions.find_one({\"session_token\": session_token}, {\"_id\": 0})
    if not sess:
        raise HTTPException(status_code=401, detail=\"Invalid session\")

    expires_at = sess.get(\"expires_at\")
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail=\"Session expired\")

    user_doc = await db.users.find_one({\"user_id\": sess[\"user_id\"]}, {\"_id\": 0})
    if not user_doc:
        raise HTTPException(status_code=401, detail=\"User not found\")
    return User(**user_doc)


# ------------------ Auth Routes ------------------
class SessionExchangeBody(BaseModel):
    session_id: str


@api_router.post(\"/auth/session\")
async def exchange_session(body: SessionExchangeBody, response: Response):
    async with httpx.AsyncClient(timeout=20.0) as http:
        r = await http.get(
            \"https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data\",
            headers={\"X-Session-ID\": body.session_id},
        )
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail=\"Invalid session id\")
    data = r.json()

    # Upsert user by email
    existing = await db.users.find_one({\"email\": data[\"email\"]}, {\"_id\": 0})
    if existing:
        user_id = existing[\"user_id\"]
        await db.users.update_one(
            {\"user_id\": user_id},
            {\"$set\": {\"name\": data.get(\"name\", existing.get(\"name\")), \"picture\": data.get(\"picture\")}},
        )
    else:
        user_id = f\"user_{uuid.uuid4().hex[:12]}\"
        await db.users.insert_one({
            \"user_id\": user_id,
            \"email\": data[\"email\"],
            \"name\": data.get(\"name\", \"\"),
            \"picture\": data.get(\"picture\"),
            \"created_at\": datetime.now(timezone.utc).isoformat(),
        })

    session_token = data[\"session_token\"]
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    await db.user_sessions.update_one(
        {\"session_token\": session_token},
        {\"$set\": {
            \"session_token\": session_token,
            \"user_id\": user_id,
            \"expires_at\": expires_at.isoformat(),
            \"created_at\": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )

    response.set_cookie(
        key=\"session_token\",
        value=session_token,
        max_age=7 * 24 * 60 * 60,
        httponly=True,
        secure=True,
        samesite=\"none\",
        path=\"/\",
    )
    user_doc = await db.users.find_one({\"user_id\": user_id}, {\"_id\": 0})
    return {\"user\": User(**user_doc).model_dump(mode=\"json\")}


@api_router.get(\"/auth/me\")
async def auth_me(user: User = Depends(get_current_user)):
    return user.model_dump(mode=\"json\")


@api_router.post(\"/auth/logout\")
async def logout(request: Request, response: Response):
    token = request.cookies.get(\"session_token\")
    if token:
        await db.user_sessions.delete_one({\"session_token\": token})
    response.delete_cookie(\"session_token\", path=\"/\", samesite=\"none\", secure=True)
    return {\"ok\": True}


# ------------------ Gemini helpers ------------------
def pdf_to_images_b64(pdf_bytes: bytes, max_pages: int = 10) -> List[str]:
    pdf = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    out = []
    n = min(len(pdf), max_pages)
    for i in range(n):
        page = pdf[i]
        pil = page.render(scale=2.0).to_pil()
        # Downscale if huge
        max_side = 1600
        if max(pil.size) > max_side:
            ratio = max_side / max(pil.size)
            pil = pil.resize((int(pil.size[0] * ratio), int(pil.size[1] * ratio)))
        buf = io.BytesIO()
        pil.convert(\"RGB\").save(buf, format=\"JPEG\", quality=85)
        import base64
        out.append(base64.b64encode(buf.getvalue()).decode(\"utf-8\"))
    return out


def image_bytes_to_b64_jpeg(data: bytes) -> str:
    import base64
    pil = Image.open(io.BytesIO(data))
    if pil.mode != \"RGB\":
        pil = pil.convert(\"RGB\")
    max_side = 1600
    if max(pil.size) > max_side:
        ratio = max_side / max(pil.size)
        pil = pil.resize((int(pil.size[0] * ratio), int(pil.size[1] * ratio)))
    buf = io.BytesIO()
    pil.save(buf, format=\"JPEG\", quality=85)
    return base64.b64encode(buf.getvalue()).decode(\"utf-8\")


async def ocr_extract(images_b64: List[str]) -> str:
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f\"ocr-{uuid.uuid4().hex[:10]}\",
        system_message=(
            \"You are an expert handwriting OCR engine specialised in UPSC Mains answer \"
            \"sheets (English). Transcribe ALL handwritten text verbatim, preserving \"
            \"paragraph breaks, headings, bullet points and diagram labels. Do not add \"
            \"commentary. If a page contains a map or diagram, write [DIAGRAM: short description].\"
        ),
    ).with_model(\"gemini\", GEMINI_MODEL)

    file_contents = [ImageContent(image_base64=b64) for b64 in images_b64]
    msg = UserMessage(
        text=\"Transcribe the handwritten UPSC Mains answer from the provided page(s).\",
        file_contents=file_contents,
    )
    text = await chat.send_message(msg)
    return text.strip() if isinstance(text, str) else str(text)


async def evaluate_answer(question: str, transcription: str) -> dict:
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f\"eval-{uuid.uuid4().hex[:10]}\",
        system_message=(
            \"You are a strict UPSC Mains examiner. Evaluate the candidate's answer on three \"
            \"criteria, each scored 0-10: (1) Content Accuracy, (2) Structure (Intro/Body/\"
            \"Conclusion), (3) Map/Diagram usage. Return ONLY valid JSON.\"
        ),
    ).with_model(\"gemini\", GEMINI_MODEL)

    schema_hint = (
        '{\"content_accuracy\": 0-10, \"structure\": 0-10, \"map_diagram\": 0-10, '
        '\"overall_score\": 0-10, \"strengths\": [\"...\"], \"improvements\": [\"...\"], '
        '\"detailed_feedback\": \"2-4 paragraphs\"}'
    )
    prompt = (
        f\"QUESTION:\n{question}\n\nCANDIDATE ANSWER (OCR transcription):\n{transcription}\n\n\"
        f\"Return strictly valid JSON matching this schema (no markdown, no prose):\n{schema_hint}\n\n\"
        \"Scoring guidelines:\n\"
        \"- Content Accuracy: factual correctness, relevance to the question, depth.\n\"
        \"- Structure: clear Introduction, well-organised Body with sub-headings/points, substantive Conclusion.\n\"
        \"- Map/Diagram: presence and quality of maps, flowcharts, or diagrams where useful (if not required by question, rate 6 as neutral).\n\"
        \"- overall_score: weighted average (0.5*content + 0.3*structure + 0.2*map).\n\"
        \"Round all scores to 1 decimal.\"
    )
    raw = await chat.send_message(UserMessage(text=prompt))
    raw = raw.strip() if isinstance(raw, str) else str(raw)
    # Try to extract JSON block
    if raw.startswith(\"```\"):
        raw = raw.strip(\"`\")
        if raw.lower().startswith(\"json\"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        # Try find the first { ... } block
        start = raw.find(\"{\")
        end = raw.rfind(\"}\")
        if start != -1 and end != -1:
            return json.loads(raw[start:end + 1])
        raise HTTPException(status_code=500, detail=\"Evaluator returned non-JSON\")


# ------------------ Evaluation Routes ------------------
@api_router.post(\"/evaluations\")
async def create_evaluation(
    file: UploadFile = File(...),
    question_reference: str = Form(...),
    user: User = Depends(get_current_user),
):
    if not question_reference or not question_reference.strip():
        raise HTTPException(status_code=400, detail=\"question_reference is required\")

    data = await file.read()
    if len(data) > 40 * 1024 * 1024:
        raise HTTPException(status_code=413, detail=\"File exceeds 40MB\")

    filename = file.filename or \"upload\"
    ftype = (file.content_type or \"\").lower()
    lower = filename.lower()

    images_b64: List[str] = []
    pages = 0
    file_kind = \"\"

    if \"pdf\" in ftype or lower.endswith(\".pdf\"):
        try:
            images_b64 = pdf_to_images_b64(data, max_pages=10)
            pages = len(images_b64)
            file_kind = \"pdf\"
        except Exception as e:
            logger.exception(\"pdf parse failed\")
            raise HTTPException(status_code=400, detail=f\"Could not parse PDF: {e}\")
    elif any(lower.endswith(ext) for ext in (\".jpg\", \".jpeg\", \".png\", \".webp\")) or ftype.startswith(\"image/\"):
        try:
            images_b64 = [image_bytes_to_b64_jpeg(data)]
            pages = 1
            file_kind = \"image\"
        except Exception as e:
            raise HTTPException(status_code=400, detail=f\"Could not read image: {e}\")
    else:
        raise HTTPException(status_code=400, detail=\"Unsupported file type. Use PDF or JPG/PNG.\")

    if not images_b64:
        raise HTTPException(status_code=400, detail=\"No pages found in file\")

    try:
        transcription = await ocr_extract(images_b64)
    except Exception as e:
        logger.exception(\"ocr failed\")
        raise HTTPException(status_code=502, detail=f\"OCR failed: {e}\")

    try:
        evalj = await evaluate_answer(question_reference, transcription)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(\"evaluation failed\")
        raise HTTPException(status_code=502, detail=f\"Evaluation failed: {e}\")

    eval_id = f\"ev_{uuid.uuid4().hex[:12]}\"
    record = {
        \"id\": eval_id,
        \"user_id\": user.user_id,
        \"question_reference\": question_reference.strip(),
        \"filename\": filename,
        \"file_type\": file_kind,
        \"pages\": pages,
        \"transcription\": transcription,
        \"overall_score\": float(evalj.get(\"overall_score\", 0)),
        \"breakdown\": {
            \"content_accuracy\": float(evalj.get(\"content_accuracy\", 0)),
            \"structure\": float(evalj.get(\"structure\", 0)),
            \"map_diagram\": float(evalj.get(\"map_diagram\", 0)),
        },
        \"strengths\": evalj.get(\"strengths\", []) or [],
        \"improvements\": evalj.get(\"improvements\", []) or [],
        \"detailed_feedback\": evalj.get(\"detailed_feedback\", \"\") or \"\",
        \"created_at\": datetime.now(timezone.utc).isoformat(),
    }
    await db.evaluations.insert_one(record.copy())
    record.pop(\"_id\", None)
    return record


@api_router.get(\"/evaluations\")
async def list_evaluations(user: User = Depends(get_current_user)):
    docs = await db.evaluations.find(
        {\"user_id\": user.user_id}, {\"_id\": 0, \"transcription\": 0}
    ).sort(\"created_at\", -1).to_list(200)
    return docs


@api_router.get(\"/evaluations/{eval_id}\")
async def get_evaluation(eval_id: str, user: User = Depends(get_current_user)):
    doc = await db.evaluations.find_one({\"id\": eval_id, \"user_id\": user.user_id}, {\"_id\": 0})
    if not doc:
        raise HTTPException(status_code=404, detail=\"Not found\")
    return doc


@api_router.delete(\"/evaluations/{eval_id}\")
async def delete_evaluation(eval_id: str, user: User = Depends(get_current_user)):
    res = await db.evaluations.delete_one({\"id\": eval_id, \"user_id\": user.user_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail=\"Not found\")
    return {\"ok\": True}


@api_router.get(\"/dashboard/stats\")
async def dashboard_stats(user: User = Depends(get_current_user)):
    docs = await db.evaluations.find(
        {\"user_id\": user.user_id}, {\"_id\": 0, \"transcription\": 0}
    ).sort(\"created_at\", 1).to_list(500)
    total = len(docs)
    avg = round(sum(d.get(\"overall_score\", 0) for d in docs) / total, 2) if total else 0
    best = max((d.get(\"overall_score\", 0) for d in docs), default=0)
    trend = [
        {
            \"date\": d[\"created_at\"][:10],
            \"score\": d.get(\"overall_score\", 0),
            \"id\": d[\"id\"],
        }
        for d in docs
    ]
    recent = sorted(docs, key=lambda d: d[\"created_at\"], reverse=True)[:5]
    return {
        \"total_evaluations\": total,
        \"average_score\": avg,
        \"best_score\": best,
        \"trend\": trend,
        \"recent\": recent,
    }


@api_router.get(\"/rubric\")
async def rubric():
    return {
        \"criteria\": [
            {
                \"name\": \"Content Accuracy\",
                \"weight\": 50,
                \"description\": \"Factual correctness, relevance to the question, and depth of analysis.\",
                \"bands\": [
                    {\"range\": \"9-10\", \"label\": \"Exceptional — comprehensive, nuanced, well-supported.\"},
                    {\"range\": \"7-8\", \"label\": \"Strong — mostly accurate with good depth.\"},
                    {\"range\": \"5-6\", \"label\": \"Average — partially relevant, missing depth.\"},
                    {\"range\": \"3-4\", \"label\": \"Below average — factual gaps or tangential.\"},
                    {\"range\": \"0-2\", \"label\": \"Poor — largely inaccurate or off-topic.\"},
                ],
            },
            {
                \"name\": \"Structure (Intro / Body / Conclusion)\",
                \"weight\": 30,
                \"description\": \"Clear introduction, organised body with sub-headings, substantive conclusion.\",
                \"bands\": [
                    {\"range\": \"9-10\", \"label\": \"Crystal clear three-part structure with smooth transitions.\"},
                    {\"range\": \"7-8\", \"label\": \"Clear structure, minor issues.\"},
                    {\"range\": \"5-6\", \"label\": \"Structure present but uneven.\"},
                    {\"range\": \"3-4\", \"label\": \"Weak structure; ideas jumbled.\"},
                    {\"range\": \"0-2\", \"label\": \"No discernible structure.\"},
                ],
            },
            {
                \"name\": \"Map / Diagram Usage\",
                \"weight\": 20,
                \"description\": \"Relevant maps, flowcharts, or diagrams that enhance the answer.\",
                \"bands\": [
                    {\"range\": \"9-10\", \"label\": \"High-value, neatly drawn, well-labelled diagrams.\"},
                    {\"range\": \"7-8\", \"label\": \"Useful diagrams, minor labelling issues.\"},
                    {\"range\": \"5-6\", \"label\": \"Basic diagram attempted.\"},
                    {\"range\": \"3-4\", \"label\": \"Diagram weak or unclear.\"},
                    {\"range\": \"0-2\", \"label\": \"No diagram where clearly expected.\"},
                ],
            },
        ],
        \"formula\": \"overall = 0.5 * content_accuracy + 0.3 * structure + 0.2 * map_diagram\",
    }


@api_router.get(\"/\")
async def root():
    return {\"message\": \"Mains Mastery API\"}


# ------------------ App setup ------------------
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get(\"CORS_ORIGINS\", \"*\").split(\",\"),
    allow_methods=[\"*\"],
    allow_headers=[\"*\"],
)


@app.on_event(\"shutdown\")
async def shutdown_db_client():
    client.close()
"
Observation: Overwrite successful: /app/backend/server.py
