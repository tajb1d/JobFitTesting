import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.schemas.resume import ResumeOut, ResumeSummary
from app.services import resumes
from app.services.embeddings import EmbeddingError, EmbeddingProvider, get_embedder
from app.services.pdf_analyzer import PdfError

router = APIRouter(prefix="/resumes", tags=["resumes"])


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found")


# Sync def: PDF parsing and matching are CPU-bound, so FastAPI runs this in its threadpool
# and the event loop stays free for other requests (and /health).
@router.post("", response_model=ResumeOut, status_code=status.HTTP_201_CREATED)
def upload_resume(
    file: UploadFile = File(..., description="Resume PDF, 5 MB max"),
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
    embedder: EmbeddingProvider = Depends(get_embedder),
) -> ResumeOut:
    data = file.file.read(resumes.MAX_PDF_BYTES + 1)  # never read more than the limit
    try:
        resume, bullets = resumes.create_resume(
            db, user_id, file.filename, file.content_type, data, embedder
        )
    except resumes.UploadError as e:
        raise HTTPException(e.status_code, str(e))
    except PdfError as e:
        # Literal 422: Starlette renamed the constant, and the old name now warns.
        raise HTTPException(422, str(e))
    except EmbeddingError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "We couldn't process your resume right now. Please try again in a minute.",
        )
    return ResumeOut.from_model(resume, bullets)


@router.get("", response_model=list[ResumeSummary])
def list_resumes(
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ResumeSummary]:
    return [ResumeSummary.model_validate(r) for r in resumes.list_resumes(db, user_id)]


@router.get("/{resume_id}", response_model=ResumeOut)
def get_resume(
    resume_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeOut:
    resume = resumes.get_resume(db, user_id, resume_id)
    if resume is None:
        raise _not_found()
    return ResumeOut.from_model(resume, resumes.get_bullets(db, resume))


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume(
    resume_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    if not resumes.delete_resume(db, user_id, resume_id):
        raise _not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
