from sqlalchemy import Column, Integer, String, BigInteger, DateTime
from datetime import datetime

from app.database import Base


class DeletionLog(Base):
    __tablename__ = "deletion_log"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    parent_title = Column(String, nullable=True)
    media_type = Column(String, nullable=False)
    library_section = Column(String, nullable=True)
    file_size = Column(BigInteger, default=0)
    arr_action = Column(String, nullable=True)  # unmonitored | deleted_from_arr | none
    deleted_at = Column(DateTime, default=datetime.utcnow, index=True)
