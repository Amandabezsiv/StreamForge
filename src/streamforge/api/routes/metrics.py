from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from streamforge.core.database import get_db
from streamforge.observability.api_metrics import render_api_metrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
def metrics(db: Annotated[Session, Depends(get_db)]) -> Response:
    content, media_type = render_api_metrics(db)
    return Response(content=content, media_type=media_type)
