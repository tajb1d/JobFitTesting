import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.schemas.analysis import AnalysisCreate, AnalysisOut, AnalysisSummary
from app.services import analyses
from app.services.embeddings import EmbeddingError, EmbeddingProvider, get_embedder
from app.services.feedback import FeedbackGenerator, get_feedback_generator

router = APIRouter(prefix="/analyses", tags=["analyses"])


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Analysis not found")


@router.post("", response_model=AnalysisOut, status_code=status.HTTP_201_CREATED)
def create_analysis(
    body: AnalysisCreate,
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
    embedder: EmbeddingProvider = Depends(get_embedder),
    feedback: FeedbackGenerator = Depends(get_feedback_generator),
) -> AnalysisOut:
    if body.job_id or body.job_url:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "Analyzing by job_id or job_url isn't available yet. Paste the job description.",
        )
    try:
        analysis = analyses.create_analysis(
            db, user_id, body.resume_id, body.job_description, body.job_title, embedder, feedback
        )
    except analyses.ResumeNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found")
    except analyses.RateLimited as e:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "You've reached the hourly analysis limit. Please try again later.",
            headers={"Retry-After": str(e.retry_after_seconds)},
        )
    except EmbeddingError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "We couldn't analyze this job right now. Please try again in a minute.",
        )
    return AnalysisOut.model_validate(analysis)


@router.get("", response_model=list[AnalysisSummary])
def list_analyses(
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AnalysisSummary]:
    return [AnalysisSummary.model_validate(a) for a in analyses.list_analyses(db, user_id)]


@router.get("/{analysis_id}", response_model=AnalysisOut)
def get_analysis(
    analysis_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalysisOut:
    analysis = analyses.get_analysis(db, user_id, analysis_id)
    if analysis is None:
        raise _not_found()
    return AnalysisOut.model_validate(analysis)


@router.delete("/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_analysis(
    analysis_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    if not analyses.delete_analysis(db, user_id, analysis_id):
        raise _not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
