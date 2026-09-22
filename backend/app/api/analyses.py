import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.schemas.analysis import AnalysisCreate, AnalysisOut, AnalysisSummary
from app.services import analyses
from app.services.embeddings import EmbeddingError, EmbeddingProvider, get_embedder
from app.services.feedback import FeedbackGenerator, get_feedback_generator
from app.services.job_urls import (
    PostingNotFound,
    PostingUnavailable,
    UnsupportedJobUrl,
    get_posting_client,
)

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
    posting_client: httpx.Client = Depends(get_posting_client),
) -> AnalysisOut:
    try:
        analysis = analyses.create_analysis(
            db, user_id, body.resume_id, embedder, feedback,
            job_description=body.job_description, job_title=body.job_title,
            job_id=body.job_id, job_url=body.job_url, posting_client=posting_client,
        )
    except UnsupportedJobUrl as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e))
    except analyses.ResumeNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found")
    except analyses.JobNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    except PostingNotFound:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "That job posting wasn't found. It may have closed. Paste the description instead.",
        )
    except PostingUnavailable:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "We couldn't reach the job board right now. Try again, or paste the description.",
        )
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
