
Action: file_editor create /app/frontend/src/pages/NewEvaluation.jsx --file-text "import React, { useRef, useState } from \"react\";
import axios from \"axios\";
import { useNavigate } from \"react-router-dom\";
import { UploadCloud, FileText, X, Sparkles } from \"lucide-react\";
import { useAuth } from \"../context/AuthContext\";

const MAX = 40 * 1024 * 1024;
const ACCEPT = [\"application/pdf\", \"image/jpeg\", \"image/png\", \"image/jpg\"];

export default function NewEvaluation() {
  const { API } = useAuth();
  const navigate = useNavigate();
  const inputRef = useRef(null);
  const [file, setFile] = useState(null);
  const [question, setQuestion] = useState(\"\");
  const [drag, setDrag] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState(\"idle\"); // idle | uploading | extracting | scoring | done
  const [error, setError] = useState(\"\");

  const onPick = (f) => {
    setError(\"\");
    if (!f) return;
    if (!ACCEPT.includes(f.type) && !/\.(pdf|jpe?g|png)$/i.test(f.name)) {
      setError(\"Only PDF, JPG or PNG files are supported.\");
      return;
    }
    if (f.size > MAX) {
      setError(\"File exceeds 40 MB limit.\");
      return;
    }
    setFile(f);
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDrag(false);
    onPick(e.dataTransfer.files?.[0]);
  };

  const submit = async () => {
    setError(\"\");
    if (!file) return setError(\"Please attach an answer sheet.\");
    if (!question.trim()) return setError(\"Question Reference is required.\");

    const fd = new FormData();
    fd.append(\"file\", file);
    fd.append(\"question_reference\", question.trim());

    setSubmitting(true);
    setStage(\"uploading\");
    setProgress(5);

    // Simulated progress while request is in-flight
    const timer = setInterval(() => {
      setProgress((p) => {
        if (p < 35) {
          setStage(\"uploading\");
          return p + 2;
        }
        if (p < 75) {
          setStage(\"extracting\");
          return p + 1.2;
        }
        if (p < 95) {
          setStage(\"scoring\");
          return p + 0.6;
        }
        return p;
      });
    }, 220);

    try {
      const r = await axios.post(`${API}/evaluations`, fd, {
        withCredentials: true,
        headers: { \"Content-Type\": \"multipart/form-data\" },
        onUploadProgress: (e) => {
          if (e.total) {
            const pct = Math.round((e.loaded / e.total) * 30);
            setProgress((p) => Math.max(p, pct));
          }
        },
      });
      clearInterval(timer);
      setProgress(100);
      setStage(\"done\");
      navigate(`/report/${r.data.id}`);
    } catch (e) {
      clearInterval(timer);
      setStage(\"idle\");
      setProgress(0);
      setError(e?.response?.data?.detail || \"Evaluation failed. Please try again.\");
    } finally {
      setSubmitting(false);
    }
  };

  const stageLabel = {
    uploading: \"Uploading your answer sheet…\",
    extracting: \"Extracting Handwriting…\",
    scoring: \"Scoring against UPSC rubric…\",
    done: \"Done!\",
  }[stage];

  return (
    <div className=\"fade-up max-w-4xl\" data-testid=\"new-evaluation-page\">
      <div className=\"text-xs uppercase tracking-[0.25em] text-[#475569]\">New Evaluation</div>
      <h1 className=\"font-display font-bold text-4xl md:text-5xl mt-2 text-[#0F172A]\">
        Upload your answer sheet
      </h1>
      <p className=\"mt-3 text-[#475569] max-w-2xl\">
        We'll OCR your handwriting with Gemini 3 Pro and score on Content, Structure and Maps/Diagrams.
      </p>

      {submitting ? (
        <ScanningView stage={stage} label={stageLabel} progress={progress} file={file} />
      ) : (
        <>
          {/* Dropzone */}
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={onDrop}
            onClick={() => inputRef.current?.click()}
            data-testid=\"dropzone\"
            className={`mt-10 cursor-pointer rounded-md min-h-[300px] flex flex-col items-center justify-center text-center p-10 transition-all border-2 border-dashed ${
              drag
                ? \"bg-[#E6F0FA] border-[#003366]\"
                : \"bg-[#F8FAFC] border-[#003366]/30 hover:border-[#003366]/60\"
            }`}
          >
            <input
              ref={inputRef}
              type=\"file\"
              accept=\".pdf,.jpg,.jpeg,.png,application/pdf,image/*\"
              className=\"hidden\"
              onChange={(e) => onPick(e.target.files?.[0])}
              data-testid=\"file-input\"
            />
            <div className=\"h-14 w-14 rounded-full bg-white border border-[#E2E8F0] grid place-items-center mb-5\">
              <UploadCloud className=\"h-6 w-6 text-[#003366]\" />
            </div>
            {file ? (
              <div className=\"flex items-center gap-3 bg-white rounded-md border border-[#E2E8F0] px-4 py-3\">
                <FileText className=\"h-5 w-5 text-[#003366]\" />
                <span className=\"text-sm font-medium text-[#0F172A]\" data-testid=\"selected-file-name\">
                  {file.name}
                </span>
                <span className=\"text-xs text-[#475569]\">
                  · {(file.size / 1024 / 1024).toFixed(2)} MB
                </span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setFile(null);
                  }}
                  className=\"ml-2 text-[#475569] hover:text-[#DC2626]\"
                  data-testid=\"clear-file-btn\"
                >
                  <X className=\"h-4 w-4\" />
                </button>
              </div>
            ) : (
              <>
                <div className=\"font-display font-semibold text-xl text-[#0F172A]\">
                  Drag & drop your answer sheet
                </div>
                <div className=\"text-sm text-[#475569] mt-1\">
                  PDF, JPG, or PNG · up to 40 MB
                </div>
                <button
                  type=\"button\"
                  className=\"btn-primary mt-6 h-10 px-5 rounded-md text-sm font-medium\"
                  data-testid=\"browse-btn\"
                >
                  Browse files
                </button>
              </>
            )}
          </div>

          {/* Question reference */}
          <div className=\"mt-8\">
            <label className=\"text-xs uppercase tracking-[0.2em] text-[#475569]\">Question Reference</label>
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              rows={4}
              data-testid=\"question-input\"
              placeholder=\"Paste the exact UPSC Mains question you attempted…\"
              className=\"mt-2 w-full rounded-md border border-[#E2E8F0] bg-white p-4 text-sm text-[#0F172A] focus:outline-none focus:ring-2 focus:ring-[#003366]/40 focus:border-[#003366]\"
            />
          </div>

          {error && (
            <div className=\"mt-4 text-sm text-[#DC2626]\" data-testid=\"error-msg\">
              {error}
            </div>
          )}

          <div className=\"mt-8 flex items-center gap-4\">
            <button
              onClick={submit}
              disabled={submitting}
              data-testid=\"submit-btn\"
              className=\"btn-primary h-12 px-7 rounded-md font-medium inline-flex items-center gap-2\"
            >
              <Sparkles className=\"h-4 w-4\" />
              Evaluate my answer
            </button>
            <span className=\"text-xs text-[#475569]\">
              Typically takes 20–60 seconds for a 1–3 page answer.
            </span>
          </div>
        </>
      )}
    </div>
  );
}

function ScanningView({ stage, label, progress, file }) {
  return (
    <div className=\"mt-10 fade-up\" data-testid=\"scanning-view\">
      <div className=\"scanning-frame h-[420px] grid place-items-center\">
        <div className=\"relative z-10 text-center px-6\">
          <div className=\"text-xs uppercase tracking-[0.3em] text-[#003366]\">UPSC Evaluator</div>
          <div className=\"font-display text-3xl md:text-4xl font-bold text-[#0F172A] mt-3 pulse-text\">
            {label}
          </div>
          <div className=\"text-sm text-[#475569] mt-3\">
            {file?.name} · {stage === \"scoring\" ? \"Applying rubric\" : \"Analysing page content\"}
          </div>
        </div>
      </div>

      <div className=\"mt-6\">
        <div className=\"flex items-center justify-between text-xs uppercase tracking-[0.2em] text-[#475569] mb-2\">
          <span>Scanning</span>
          <span data-testid=\"progress-pct\">{Math.min(100, Math.round(progress))}%</span>
        </div>
        <div className=\"h-2 bg-[#E2E8F0] rounded-full overflow-hidden\">
          <div
            className=\"h-full transition-[width] duration-300\"
            style={{
              width: `${Math.min(100, progress)}%`,
              background:
                stage === \"done\"
                  ? \"#16A34A\"
                  : \"linear-gradient(90deg,#D4AF37 0%,#E5C158 100%)\",
            }}
          />
        </div>
        <div className=\"mt-4 grid grid-cols-3 gap-2 text-[11px] uppercase tracking-[0.15em] text-[#475569]\">
          <StageDot active={[\"uploading\", \"extracting\", \"scoring\", \"done\"].includes(stage)} label=\"Upload\" />
          <StageDot active={[\"extracting\", \"scoring\", \"done\"].includes(stage)} label=\"Handwriting\" />
          <StageDot active={[\"scoring\", \"done\"].includes(stage)} label=\"Score\" />
        </div>
      </div>
    </div>
  );
}

function StageDot({ active, label }) {
  return (
    <div className=\"flex items-center gap-2\">
      <span
        className={`h-2 w-2 rounded-full ${active ? \"\" : \"bg-[#E2E8F0]\"}`}
        style={active ? { backgroundColor: \"#003366\" } : {}}
      />
      {label}
    </div>
  );
}
"
Observation: Create successful: /app/frontend/src/pages/NewEvaluation.jsx
