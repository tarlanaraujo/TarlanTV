from app import db
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, Integer, DateTime, Boolean

class SearchHistory(db.Model):
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=True)
    channels_found: Mapped[int] = mapped_column(Integer, default=0)
    valid_channels: Mapped[int] = mapped_column(Integer, default=0)
    search_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(50), default='pending')
    
class Channel(db.Model):
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=True)
    logo: Mapped[str] = mapped_column(String(500), nullable=True)
    group: Mapped[str] = mapped_column(String(100), nullable=True)
    is_working: Mapped[bool] = mapped_column(Boolean, default=None)
    search_history_id: Mapped[int] = mapped_column(Integer, db.ForeignKey('search_history.id'), nullable=False)
    last_checked: Mapped[datetime] = mapped_column(DateTime, nullable=True)

class PlaylistExport(db.Model):
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    channels_count: Mapped[int] = mapped_column(Integer, default=0)
    export_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    export_type: Mapped[str] = mapped_column(String(50), default='m3u')
