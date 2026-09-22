import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.services.account import AuthAdmin, AuthAdminError, delete_account, get_auth_admin

router = APIRouter(tags=["account"])


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_account(
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
    admin: AuthAdmin = Depends(get_auth_admin),
) -> Response:
    try:
        delete_account(db, user_id, admin)
    except AuthAdminError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Your data was deleted, but we couldn't remove your login. Please try again.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
