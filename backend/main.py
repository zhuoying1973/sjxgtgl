import asyncio
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional, List, Dict, Any, Tuple, Union, Set
from sqlalchemy import inspect
import math
import calendar
import io
from PIL import Image
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status, BackgroundTasks, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, create_engine, func, select, text, Boolean, Column, Float, Text, or_, desc
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker, joinedload, declarative_base, selectinload


from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import PlainTextResponse
import traceback
from backend.i18n import I18n

# 动态检测并自动安装 pypinyin 库
try:
    import pypinyin
except ImportError:
    import subprocess
    import sys
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pypinyin"])
        import pypinyin
    except Exception as e:
        print(f"Failed to auto-install pypinyin: {e}")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_SQLITE_DB_FILE = os.path.abspath(os.path.join(_BASE_DIR, "..", "data", "app.db"))

def _score_sqlite_db_file(path: str) -> int:
    try:
        conn = sqlite3.connect(path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0] for r in cur.fetchall() if r and r[0]}
            key_tables = {"users", "projects", "clients", "work_items", "commission_rules"}
            score = len(tables & key_tables) * 100
            if "users" in tables:
                try:
                    cur.execute("SELECT COUNT(1) FROM users")
                    score += int(cur.fetchone()[0] or 0)
                except Exception:
                    pass
            return score
        finally:
            conn.close()
    except Exception:
        return -1

def _select_default_sqlite_db_file() -> str:
    candidates = [
        os.path.abspath(os.path.join(_BASE_DIR, "..", "data", "app.db")),
        os.path.abspath(os.path.join(_BASE_DIR, "data", "app.db")),
        os.path.abspath("./data/app.db"),
    ]
    best_path = ""
    best_score = -1
    for p in candidates:
        if not os.path.exists(p):
            continue
        s = _score_sqlite_db_file(p)
        if s > best_score:
            best_score = s
            best_path = p
    return best_path or candidates[0]

def _build_default_database_url() -> str:
    p = _select_default_sqlite_db_file()
    return "sqlite:///" + p.replace("\\", "/")


APP_SECRET_KEY = os.environ.get("APP_SECRET_KEY", "CHANGE_ME")
DATABASE_URL = os.environ.get("DATABASE_URL") or _build_default_database_url()
BACKUP_TARGET_PATH = os.environ.get("BACKUP_TARGET_PATH", "")
BACKUP_INTERVAL_MINUTES = int(os.environ.get("BACKUP_INTERVAL_MINUTES", "60"))

DEFAULT_ADMIN_USERNAME = os.environ.get("DEFAULT_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("DEFAULT_ADMIN_PASSWORD", "admin123")
APP_VERSION = "1.0.5"
APP_COPYRIGHT = "© 2026 晟景设计版权. All Rights Reserved."

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def _hash_password(password: str) -> str:
    return pwd_context.hash(password)

def _verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(30), default="staff")  # admin/manager/staff/finance
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    can_view_logs: Mapped[bool] = mapped_column(Boolean, default=False)  # Admin always true, Manager depends on this
    skills: Mapped[Optional[str]] = mapped_column(String(100), default="")  # 专业工序岗位
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否被锁定
    is_initial_password: Mapped[bool] = mapped_column(Boolean, default=True)  # 是否为初始/重置未修改密码
    initial_pwd_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # 初始/重置时间
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    tasks: Mapped[List["WorkItem"]] = relationship(back_populates="assignee")

class BackupConfig(Base):
    __tablename__ = "backup_config"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backup_path: Mapped[str] = mapped_column(String(500), default="")
    backup_prefix: Mapped[str] = mapped_column(String(100), default="backup_")
    max_backups: Mapped[int] = mapped_column(Integer, default=10)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    
    updater: Mapped["User"] = relationship()


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(500), default="")
    description: Mapped[str] = mapped_column(String(500), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get_val(cls, db: Session, key: str, default: str = "") -> str:
        s = db.get(cls, key)
        return s.value if s else default

class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    client_type: Mapped[str] = mapped_column(String(50), default="company")  # company/design_institute/developer/individual
    tax_id: Mapped[str] = mapped_column(String(100), default="")
    address: Mapped[str] = mapped_column(String(500), default="")
    notes: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[int] = mapped_column(Integer, default=1)  # 1-active, 0-inactive
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关系
    contacts: Mapped[List["Contact"]] = relationship("Contact", back_populates="client", cascade="all, delete-orphan")
    projects: Mapped[List["Project"]] = relationship("Project", back_populates="client")

class Contact(Base):
    __tablename__ = "contact_persons"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    position: Mapped[str] = mapped_column(String(100), default="")  # 职位
    department: Mapped[str] = mapped_column(String(100), default="")  # 部门
    phone: Mapped[str] = mapped_column(String(50), default="")  # 座机
    mobile: Mapped[str] = mapped_column(String(50), default="")  # 手机
    email: Mapped[str] = mapped_column(String(100), default="")
    wechat: Mapped[str] = mapped_column(String(100), default="")  # 微信
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否为主要联系人
    notes: Mapped[str] = mapped_column(String(1000), default="")  # 备注
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关系
    client: Mapped["Client"] = relationship("Client", back_populates="contacts")
    projects: Mapped[List["Project"]] = relationship("Project", back_populates="primary_contact")

class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id"), nullable=True)
    primary_contact_id: Mapped[Optional[int]] = mapped_column(ForeignKey("contact_persons.id"), nullable=True)
    manager_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    client_name: Mapped[str] = mapped_column(String(200), default="")  # 保留旧字段，用于向后兼容
    contact_person: Mapped[str] = mapped_column(String(100), default="")
    code: Mapped[str] = mapped_column(String(20), default="")
    status: Mapped[str] = mapped_column(String(50), default="进行中")
    status_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("100.00"))
    final_price_override: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client: Mapped[Optional["Client"]] = relationship("Client", back_populates="projects")
    primary_contact: Mapped[Optional["Contact"]] = relationship("Contact", back_populates="projects")
    manager: Mapped[Optional["User"]] = relationship("User", foreign_keys=[manager_id])
    tasks: Mapped[List["WorkItem"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    design_items: Mapped[List["ProjectDesignItem"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    logs: Mapped[List["ProjectLog"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    is_deleted: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deleted_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # --- AI 与 极简重构版 新增字段 ---
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("5.00"))  # 税率
    ai_factor: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("100.00")) # AI 折收因子 (%, 100表示不折收)
    
    # 指派的工序比例 (由项目经理设置，存储为简单 JSON 字符串或是扩展字段)
    ratio_scheme: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # {"方案": 5, "建模": 12, "渲染": 5, "后期": 8}
    local_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True) # 局域网大文件共享路径

class DesignService(Base):
    __tablename__ = "design_services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    base_price: Mapped[Optional[str]] = mapped_column(String(200), default="0")
    internal_price: Mapped[Optional[str]] = mapped_column(String(200), default="0")  # 对内价格区间
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    custom_attrs_a: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # 修饰 A 选项 (正面,侧面...)
    custom_attrs_b: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # 修饰 B 选项 (日景,夜景...)

    project_items: Mapped[List["ProjectDesignItem"]] = relationship(back_populates="service")

class ProjectDesignItem(Base):
    __tablename__ = "project_design_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    service_id: Mapped[int] = mapped_column(ForeignKey("design_services.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("1.00"))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    internal_unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))  # 对内单价快照
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    custom_prefix: Mapped[Optional[str]] = mapped_column(String(100), nullable=True) # 前缀(手写)
    custom_attr_a: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 修饰 A 选择值
    custom_attr_b: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 修饰 B 选择值

    project: Mapped[Project] = relationship(back_populates="design_items")
    service: Mapped[DesignService] = relationship(back_populates="project_items")

    @property
    def full_name(self) -> str:
        parts = []
        if self.custom_prefix:
            parts.append(self.custom_prefix)
        if self.custom_attr_a:
            parts.append(self.custom_attr_a)
        if self.custom_attr_b:
            parts.append(self.custom_attr_b)
        parts.append(self.service.name if self.service else "")
        return "".join(parts)

class CommissionRule(Base):
    __tablename__ = "commission_rules"
    __table_args__ = (UniqueConstraint("role", "unit_type", name="uq_commission_role_unit"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String(30))  # staff/manager/etc
    unit_type: Mapped[str] = mapped_column(String(30), default="point")
    rate_per_unit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))

class WorkItem(Base):
    __tablename__ = "work_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(String(200))
    stage: Mapped[str] = mapped_column(String(50), default="设计")

    assigned_to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    workload_units: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    unit_type: Mapped[str] = mapped_column(String(30), default="point")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    project: Mapped[Project] = relationship(back_populates="tasks")
    assignee: Mapped[User] = relationship(back_populates="tasks")
    
    # New fields for workflow
    status: Mapped[str] = mapped_column(String(20), default="待办")  # 待办, 进行中, 审核中, 已完成
    source_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("project_design_items.id"), nullable=True)
    
    source_item: Mapped[Optional["ProjectDesignItem"]] = relationship()
    
    images: Mapped[List["TaskImage"]] = relationship(back_populates="task", cascade="all, delete-orphan")

class TaskImage(Base):
    __tablename__ = "task_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("work_items.id"), index=True)
    file_path: Mapped[str] = mapped_column(String(500))
    thumbnail_path: Mapped[str] = mapped_column(String(500), nullable=True)
    file_size_kb: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped["WorkItem"] = relationship("WorkItem", back_populates="images")

class ProjectLog(Base):
    __tablename__ = "project_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(50))  # CREATE, UPDATE, DELETE, ADD_ITEM, REMOVE_ITEM...
    details: Mapped[str] = mapped_column(String(2000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = relationship(back_populates="logs")
    user: Mapped["User"] = relationship()

class ActionLog(Base):
    __tablename__ = "action_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    target_type: Mapped[str] = mapped_column(String(50))  # project, task, client, etc.
    target_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(20))  # create, update, delete
    old_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string
    new_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string
    details: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_undone: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped["User"] = relationship()

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def get_db() -> Session:
    db = SessionLocal()
    try:
        ensure_runtime_schema_if_needed(db)
        yield db
    finally:
        db.close()


_schema_ready = False
_schema_lock = threading.Lock()


def ensure_runtime_schema_if_needed(db: Session) -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        try:
            _run_schema_step(db, "migrate_commission_rules_schema_if_needed", migrate_commission_rules_schema_if_needed)
            _run_schema_step(db, "migrate_projects_local_path_if_needed", migrate_projects_local_path_if_needed)
            _run_schema_step(db, "migrate_projects_pricing_if_needed", migrate_projects_pricing_if_needed)
            _run_schema_step(db, "migrate_projects_status_changed_at_if_needed", migrate_projects_status_changed_at_if_needed)
            _run_schema_step(db, "migrate_projects_code_if_needed", migrate_projects_code_if_needed)
            _run_schema_step(db, "migrate_design_services_order_if_needed", migrate_design_services_order_if_needed)
            _run_schema_step(db, "migrate_clients_table_if_needed", migrate_clients_table_if_needed)
            _run_schema_step(db, "migrate_contact_persons_schema_if_needed", migrate_contact_persons_schema_if_needed)
            _run_schema_step(db, "migrate_work_items_schema_if_needed", migrate_work_items_schema_if_needed)
            _run_schema_step(db, "migrate_task_images_schema_if_needed", migrate_task_images_schema_if_needed)
            _run_schema_step(db, "migrate_projects_soft_delete_if_needed", migrate_projects_soft_delete_if_needed)
            _run_schema_step(db, "migrate_project_logs_schema_if_needed", migrate_project_logs_schema_if_needed)
            _run_schema_step(db, "migrate_project_logs_schema_if_needed", migrate_project_logs_schema_if_needed)
            _run_schema_step(db, "migrate_users_permissions_if_needed", migrate_users_permissions_if_needed)
            _run_schema_step(db, "migrate_users_permissions_if_needed", migrate_users_permissions_if_needed)
            _run_schema_step(db, "migrate_internal_pricing_schema_if_needed", migrate_internal_pricing_schema_if_needed)
            _run_schema_step(db, "migrate_system_settings_schema_if_needed", migrate_system_settings_schema_if_needed)
            _run_schema_step(db, "migrate_task_status_to_chinese_if_needed", migrate_task_status_to_chinese_if_needed)
            _run_schema_step(db, "migrate_custom_fields_schema_if_needed", migrate_custom_fields_schema_if_needed)
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
            raise
        _schema_ready = True


def _run_schema_step(db: Session, name: str, fn) -> None:
    try:
        fn(db)
    except Exception as e:
        raise RuntimeError(f"schema step failed: {name}: {type(e).__name__}: {e}")

def ensure_dirs() -> None:
    if DATABASE_URL.startswith("sqlite"):
        db_file = _sqlite_db_file_path()
        if db_file:
            os.makedirs(os.path.dirname(db_file), exist_ok=True)
        else:
            os.makedirs("./data", exist_ok=True)

def _d2(v: Decimal) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"))

def _parse_decimal(value: str, default: Optional[Decimal] = None) -> Optional[Decimal]:
    s = str(value).strip()
    if not s:
        return default
    try:
        return Decimal(s)
    except Exception:
        return default

def bootstrap(db: Session) -> None:
    existing = db.scalar(select(func.count(User.id)))
    if existing and existing > 0:
        return

    admin = User(
        username=DEFAULT_ADMIN_USERNAME,
        full_name="管理员",
        role="admin",
        password_hash=_hash_password(DEFAULT_ADMIN_PASSWORD),
        is_active=1,
    )
    db.add(admin)

    db.add_all(
        [
            CommissionRule(role="staff", unit_type="point", rate_per_unit=Decimal("50.00")),
            CommissionRule(role="staff", unit_type="sheet", rate_per_unit=Decimal("30.00")),
            CommissionRule(role="manager", unit_type="point", rate_per_unit=Decimal("80.00")),
            CommissionRule(role="manager", unit_type="sheet", rate_per_unit=Decimal("50.00")),
            CommissionRule(role="finance", unit_type="point", rate_per_unit=Decimal("0.00")),
            CommissionRule(role="finance", unit_type="sheet", rate_per_unit=Decimal("0.00")),
            CommissionRule(role="admin", unit_type="point", rate_per_unit=Decimal("0.00")),
            CommissionRule(role="admin", unit_type="sheet", rate_per_unit=Decimal("0.00")),
        ]
    )

    demo_project = Project(
        name="示例项目",
        client_name="示例客户",
        status="进行中",
        status_changed_at=datetime.utcnow(),
    )
    db.add(demo_project)
    db.flush()

    demo_task = WorkItem(
        project_id=demo_project.id,
        title="出一张主视角效果图",
        stage="出图",
        assigned_to_user_id=admin.id,
        workload_units=Decimal("1.00"),
        unit_type="point",
    )
    db.add(demo_task)

    db.commit()


def migrate_contact_persons_schema_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return

    try:
        cols = db.execute(text("PRAGMA table_info('contact_persons')")).fetchall()
    except Exception:
        return

    names = {c[1] for c in cols if len(c) >= 2}
    if not names:
        return

    # Add columns used by templates/queries
    if "position" not in names:
        db.execute(text("ALTER TABLE contact_persons ADD COLUMN position VARCHAR(100) NOT NULL DEFAULT ''"))
    if "department" not in names:
        db.execute(text("ALTER TABLE contact_persons ADD COLUMN department VARCHAR(100) NOT NULL DEFAULT ''"))
    if "phone" not in names:
        db.execute(text("ALTER TABLE contact_persons ADD COLUMN phone VARCHAR(50) NOT NULL DEFAULT ''"))
    if "mobile" not in names:
        db.execute(text("ALTER TABLE contact_persons ADD COLUMN mobile VARCHAR(50) NOT NULL DEFAULT ''"))
    if "email" not in names:
        db.execute(text("ALTER TABLE contact_persons ADD COLUMN email VARCHAR(100) NOT NULL DEFAULT ''"))
    if "wechat" not in names:
        db.execute(text("ALTER TABLE contact_persons ADD COLUMN wechat VARCHAR(100) NOT NULL DEFAULT ''"))
    if "is_primary" not in names:
        db.execute(text("ALTER TABLE contact_persons ADD COLUMN is_primary BOOLEAN NOT NULL DEFAULT 0"))
    if "notes" not in names:
        db.execute(text("ALTER TABLE contact_persons ADD COLUMN notes VARCHAR(1000) NOT NULL DEFAULT ''"))
    if "created_at" not in names:
        db.execute(text("ALTER TABLE contact_persons ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"))
    if "updated_at" not in names:
        db.execute(text("ALTER TABLE contact_persons ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"))

    db.commit()


def migrate_work_items_schema_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return

    try:
        cols = db.execute(text("PRAGMA table_info('work_items')")).fetchall()
    except Exception:
        return

    names = {c[1] for c in cols if len(c) >= 2}
    if not names:
        return

    if "stage" not in names:
        db.execute(text("ALTER TABLE work_items ADD COLUMN stage VARCHAR(50) NOT NULL DEFAULT '设计'"))
    if "assigned_to_user_id" not in names:
        db.execute(text("ALTER TABLE work_items ADD COLUMN assigned_to_user_id INTEGER"))
    if "workload_units" not in names:
        db.execute(text("ALTER TABLE work_items ADD COLUMN workload_units NUMERIC(12,2) NOT NULL DEFAULT 0"))
    if "unit_type" not in names:
        db.execute(text("ALTER TABLE work_items ADD COLUMN unit_type VARCHAR(30) NOT NULL DEFAULT 'point'"))
    if "created_at" not in names:
        db.execute(text("ALTER TABLE work_items ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"))
    if "completed_at" not in names:
        db.execute(text("ALTER TABLE work_items ADD COLUMN completed_at DATETIME NULL"))
    
    # New columns for workflow
    if "status" not in names:
        db.execute(text("ALTER TABLE work_items ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'pending'"))
        # Migrate existing completed tasks
        db.execute(text("UPDATE work_items SET status = 'done' WHERE completed_at IS NOT NULL"))
        
    if "source_item_id" not in names:
        db.execute(text("ALTER TABLE work_items ADD COLUMN source_item_id INTEGER NULL"))

    db.commit()

def migrate_task_images_schema_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return
    
    # Check if table exists
    try:
        # Use inspection
        con = db.connection()
        # Simple check by trying to select from it, or use inspector
        # Since we are in a function, let's use the model's create
        # But we need to check if it exists first to avoid error
        inspector = inspect(db.get_bind())
        if not inspector.has_table("task_images"):
            TaskImage.__table__.create(bind=db.get_bind())
    except Exception as e:
        print(f"Error checking/creating task_images table: {e}")

def migrate_task_status_to_chinese_if_needed(db: Session) -> None:
    # Check if there are any old English status values
    try:
        check = db.execute(text("SELECT count(*) FROM work_items WHERE status IN ('pending', 'processing', 'review', 'done')")).scalar()
        if check and check > 0:
            db.execute(text("UPDATE work_items SET status = '待办' WHERE status = 'pending'"))
            db.execute(text("UPDATE work_items SET status = '进行中' WHERE status = 'processing'"))
            db.execute(text("UPDATE work_items SET status = '审核中' WHERE status = 'review'"))
            db.execute(text("UPDATE work_items SET status = '已完成' WHERE status = 'done'"))
            db.commit()
    except Exception as e:
        print(f"Migration migrate_task_status_to_chinese_if_needed failed: {e}")

def migrate_projects_code_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return

    try:
        cols = db.execute(text("PRAGMA table_info('projects')")).fetchall()
    except Exception as e:
        print("migrate_projects_code_if_needed: failed to read projects columns:", e)
        return

    names = {c[1] for c in cols if len(c) >= 2}
    if "code" not in names:
        try:
            db.execute(text("ALTER TABLE projects ADD COLUMN code VARCHAR(20) NOT NULL DEFAULT ''"))
            db.commit()
        except Exception as e:
            print("migrate_projects_code_if_needed: failed to add column:", e)
            db.rollback()
            return

def _format_dt(v: object) -> str:
    if not v:
        return ""
    try:
        # datetime/date-like
        if hasattr(v, "strftime"):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        return str(v)
    except Exception:
        return str(v)

def _format_date_only(v: object) -> str:
    if not v:
        return ""
    try:
        if hasattr(v, "strftime"):
            return v.strftime("%Y-%m-%d")
        val_str = str(v)
        if len(val_str) >= 10:
            return val_str[:10]
        return val_str
    except Exception:
        return str(v)

def _month_range(dt: datetime) -> Tuple[datetime, datetime]:
    start = datetime(dt.year, dt.month, 1)
    if dt.month == 12:
        end = datetime(dt.year + 1, 1, 1)
    else:
        end = datetime(dt.year, dt.month + 1, 1)
    return start, end

def _yymm(dt: datetime) -> str:
    return f"{dt.year % 100:02d}{dt.month:02d}"

def normalize_project_codes_for_month(db: Session, dt: datetime) -> None:
    """按项目创建月份重排编号（YYMM###），用于删除/修复断号。"""
    migrate_projects_code_if_needed(db)
    start, end = _month_range(dt)
    month_projects = db.scalars(
        select(Project)
        .where(Project.created_at >= start, Project.created_at < end, Project.is_deleted == 0)
        .order_by(Project.created_at.asc(), Project.id.asc())
    ).all()
    prefix = _yymm(dt)
    for idx, p in enumerate(month_projects, start=1):
        p.code = f"{prefix}{idx:03d}"
        db.add(p)

def _next_project_code_for_month(db: Session, dt: datetime) -> str:
    migrate_projects_code_if_needed(db)
    start, end = _month_range(dt)
    prefix = _yymm(dt)

    codes = db.scalars(
        select(Project.code)
        .where(Project.created_at >= start, Project.created_at < end, Project.code.is_not(None))
    ).all()

    existing_seqs = set()
    for c in codes:
        s = (c or "").strip()
        # 兼容旧7位和新6位，优先生成规则: 检测7位
        if len(s) == 7 and s.isdigit() and s.startswith(prefix):
            try:
                existing_seqs.add(int(s[-3:]))
            except Exception:
                continue
    
    # Gap Filling Logic: Find the first missing number starting from 1
    seq = 1
    while seq in existing_seqs:
        seq += 1
        
    return f"{prefix}{seq:03d}"

def backfill_blank_project_codes_for_month(db: Session, dt: datetime) -> None:
    """仅回填空编号，不覆盖已有（可能是手动修改过的）编号。"""
    migrate_projects_code_if_needed(db)
    start, end = _month_range(dt)
    prefix = _yymm(dt)

    month_projects = db.scalars(
        select(Project)
        .where(Project.created_at >= start, Project.created_at < end, Project.is_deleted == 0)
        .order_by(Project.created_at.asc(), Project.id.asc())
    ).all()

    used: Set[int] = set()
    for p in month_projects:
        s = (p.code or "").strip()
        if len(s) == 7 and s.isdigit() and s.startswith(prefix):
            used.add(int(s[-3:]))

    seq = 1
    for p in month_projects:
        if (p.code or "").strip():
            continue
        while seq in used:
            seq += 1
        p.code = f"{prefix}{seq:03d}"
        used.add(seq)
        seq += 1
        db.add(p)

def ensure_project_code(db: Session, project: Project) -> None:
    """确保项目拥有编号；若缺失则生成同月下一号（不覆盖已有）。"""
    migrate_projects_code_if_needed(db)
    if (project.code or "").strip():
        return
    project.code = _next_project_code_for_month(db, project.created_at)
    db.add(project)

def migrate_projects_local_path_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return
    try:
        cols = db.execute(text("PRAGMA table_info('projects')")).fetchall()
        names = {c[1] for c in cols if len(c) >= 2}
        if "local_path" not in names:
            db.execute(text("ALTER TABLE projects ADD COLUMN local_path VARCHAR(500) NULL"))
            db.commit()
    except Exception as e:
        print(f"migrate_projects_local_path_if_needed failed: {e}")
        db.rollback()

def migrate_projects_status_changed_at_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return

    try:
        cols = db.execute(text("PRAGMA table_info('projects')")).fetchall()
    except Exception as e:
        print("migrate_projects_status_changed_at_if_needed: failed to read projects columns:", e)
        return

    names = {c[1] for c in cols if len(c) >= 2}
    if "status_changed_at" not in names:
        try:
            db.execute(text("ALTER TABLE projects ADD COLUMN status_changed_at DATETIME"))
            db.commit()
        except Exception as e:
            print("migrate_projects_status_changed_at_if_needed: failed to add column:", e)
            db.rollback()
            return

    # 回填历史数据：仅填空值
    db.execute(
        text(
            "UPDATE projects SET status_changed_at = COALESCE(status_changed_at, updated_at, created_at)"
        )
    )
    db.commit()

def migrate_design_services_order_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return
    
    try:
        cols = db.execute(text("PRAGMA table_info('design_services')")).fetchall()
    except Exception:
        return

    names = {c[1] for c in cols if len(c) >= 2}
    if "sort_order" not in names:
        db.execute(text("ALTER TABLE design_services ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"))
        db.commit()

    # backfill for existing rows
    db.execute(text("UPDATE design_services SET sort_order = id * 10 WHERE sort_order IS NULL OR sort_order = 0"))
    db.commit()

def migrate_internal_pricing_schema_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return

    # 1. DesignService.internal_price
    try:
        cols = db.execute(text("PRAGMA table_info('design_services')")).fetchall()
        names = {c[1] for c in cols if len(c) >= 2}
        if "internal_price" not in names:
            db.execute(text("ALTER TABLE design_services ADD COLUMN internal_price NUMERIC(12,2) NOT NULL DEFAULT 0.00"))
            # Initialize internal_price same as base_price for existing
            db.execute(text("UPDATE design_services SET internal_price = base_price"))
            db.commit()
    except Exception as e:
        print("migrate_internal_pricing_schema_if_needed (design_services):", e)

    # 2. ProjectDesignItem.internal_unit_price
    try:
        cols = db.execute(text("PRAGMA table_info('project_design_items')")).fetchall()
        names = {c[1] for c in cols if len(c) >= 2}
        if "internal_unit_price" not in names:
            db.execute(text("ALTER TABLE project_design_items ADD COLUMN internal_unit_price NUMERIC(12,2) NOT NULL DEFAULT 0.00"))
            # Initialize with unit_price for existing lines (fallback)
            db.execute(text("UPDATE project_design_items SET internal_unit_price = unit_price"))
            db.commit()
    except Exception as e:
        print("migrate_internal_pricing_schema_if_needed (project_design_items):", e)

    try:
        cols = db.execute(text("PRAGMA table_info('design_services')")).fetchall()
    except Exception:
        return

    names = {c[1] for c in cols if len(c) >= 2}
    if "sort_order" not in names:
        db.execute(text("ALTER TABLE design_services ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"))
        db.commit()

    # backfill for existing rows
    db.execute(text("UPDATE design_services SET sort_order = id * 10 WHERE sort_order IS NULL OR sort_order = 0"))
    db.commit()

def migrate_projects_pricing_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return

    try:
        cols = db.execute(text("PRAGMA table_info('projects')")).fetchall()
    except Exception:
        return

    names = {c[1] for c in cols if len(c) >= 2}

    if "discount_percent" not in names:
        db.execute(text("ALTER TABLE projects ADD COLUMN discount_percent NUMERIC(6,2) NOT NULL DEFAULT 100.00"))
    if "final_price_override" not in names:
        db.execute(text("ALTER TABLE projects ADD COLUMN final_price_override NUMERIC(12,2) NULL"))

    db.commit()

def ensure_admin_password_hash(db: Session) -> None:
    admin = db.scalar(select(User).where(User.username == DEFAULT_ADMIN_USERNAME))
    if not admin:
        return

    # If the project previously used bcrypt (or any other scheme), switching to pbkdf2_sha256
    # will make verification fail. For beginner-friendly operation, we reset/migrate the
    # default admin password to the configured DEFAULT_ADMIN_PASSWORD.
    if not str(admin.password_hash).startswith("$pbkdf2-sha256$"):
        admin.password_hash = _hash_password(DEFAULT_ADMIN_PASSWORD)
        db.add(admin)
        db.commit()

def migrate_projects_soft_delete_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return
    try:
        cols = db.execute(text("PRAGMA table_info('projects')")).fetchall()
    except Exception:
        return

    names = {c[1] for c in cols if len(c) >= 2}

    if "is_deleted" not in names:
        db.execute(text("ALTER TABLE projects ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0"))
    if "deleted_at" not in names:
        db.execute(text("ALTER TABLE projects ADD COLUMN deleted_at DATETIME NULL"))
    if "deleted_by" not in names:
        db.execute(text("ALTER TABLE projects ADD COLUMN deleted_by INTEGER NULL"))
    db.commit()

def migrate_projects_manager_id_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return
    try:
        cols = db.execute(text("PRAGMA table_info('projects')")).fetchall()
        names = {c[1] for c in cols if len(c) >= 2}
        if "manager_id" not in names:
            db.execute(text("ALTER TABLE projects ADD COLUMN manager_id INTEGER NULL"))
            db.commit()
    except Exception as e:
        print(f"migrate_projects_manager_id_if_needed failed: {e}")
        db.rollback()

def migrate_custom_fields_schema_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return
    # 迁移 design_services 表，增加 custom_attrs_a 和 custom_attrs_b 列
    try:
        cols_svc = db.execute(text("PRAGMA table_info('design_services')")).fetchall()
        names_svc = {c[1] for c in cols_svc if len(c) >= 2}
        if "custom_attrs_a" not in names_svc:
            db.execute(text("ALTER TABLE design_services ADD COLUMN custom_attrs_a TEXT NULL"))
        if "custom_attrs_b" not in names_svc:
            db.execute(text("ALTER TABLE design_services ADD COLUMN custom_attrs_b TEXT NULL"))
        db.commit()
    except Exception as e:
        db.rollback()
        print("迁移 design_services 增加修饰词字段配置列失败:", e)

    # 迁移 project_design_items 表，增加 custom_prefix, custom_attr_a, custom_attr_b 列
    try:
        cols_item = db.execute(text("PRAGMA table_info('project_design_items')")).fetchall()
        names_item = {c[1] for c in cols_item if len(c) >= 2}
        if "custom_prefix" not in names_item:
            db.execute(text("ALTER TABLE project_design_items ADD COLUMN custom_prefix VARCHAR(100) NULL"))
        if "custom_attr_a" not in names_item:
            db.execute(text("ALTER TABLE project_design_items ADD COLUMN custom_attr_a VARCHAR(50) NULL"))
        if "custom_attr_b" not in names_item:
            db.execute(text("ALTER TABLE project_design_items ADD COLUMN custom_attr_b VARCHAR(50) NULL"))
        db.commit()
    except Exception as e:
        db.rollback()
        print("迁移 project_design_items 增加修饰值列失败:", e)

def migrate_project_logs_schema_if_needed(db: Session) -> None:
    inspector = inspect(engine)
    if "project_logs" not in inspector.get_table_names():
        Base.metadata.create_all(bind=engine, tables=[ProjectLog.__table__])
        db.commit()

def migrate_users_permissions_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return
    try:
        cols = db.execute(text("PRAGMA table_info('users')")).fetchall()
    except Exception:
        return
    names = {c[1] for c in cols if len(c) >= 2}
    if "can_view_logs" not in names:
        db.execute(text("ALTER TABLE users ADD COLUMN can_view_logs BOOLEAN NOT NULL DEFAULT 0"))
        db.commit()

def migrate_users_skills_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return
    try:
        cols = db.execute(text("PRAGMA table_info('users')")).fetchall()
    except Exception:
        return
    names = {c[1] for c in cols if len(c) >= 2}
    if "skills" not in names:
        db.execute(text("ALTER TABLE users ADD COLUMN skills VARCHAR(100) DEFAULT ''"))
        db.commit()

def migrate_users_security_fields_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return
    try:
        cols = db.execute(text("PRAGMA table_info('users')")).fetchall()
    except Exception:
        return
    names = {c[1] for c in cols if len(c) >= 2}
    
    modified = False
    if "is_locked" not in names:
        db.execute(text("ALTER TABLE users ADD COLUMN is_locked BOOLEAN NOT NULL DEFAULT 0"))
        modified = True
    if "is_initial_password" not in names:
        db.execute(text("ALTER TABLE users ADD COLUMN is_initial_password BOOLEAN NOT NULL DEFAULT 0"))
        modified = True
    if "initial_pwd_at" not in names:
        db.execute(text("ALTER TABLE users ADD COLUMN initial_pwd_at DATETIME NULL"))
        modified = True
        
    if modified:
        db.commit()

def migrate_system_settings_schema_if_needed(db: Session) -> None:
    inspector = inspect(engine)
    if "system_settings" not in inspector.get_table_names():
        Base.metadata.create_all(bind=engine, tables=[SystemSetting.__table__])
        # Seed default values
        defaults = [
            SystemSetting(key="commission_rate_min", value="0.15", description="提成比例下限预警"),
            SystemSetting(key="commission_rate_warning", value="0.20", description="提成比例预警阈值"),
            SystemSetting(key="commission_rate_max", value="0.25", description="提成比例上限强提示"),
        ]
        db.add_all(defaults)
        db.commit()
    ensure_system_settings(db)

def ensure_system_settings(db: Session) -> None:
    defaults = {
        "commission_rate_min": ("0.15", "提成比例下限预警"),
        "commission_rate_warning": ("0.20", "提成比例预警阈值"),
        "commission_rate_max": ("0.25", "提成比例上限强提示"),
        "price_modeling": ("80.00", "建模计件默认单价"),
        "price_rendering": ("50.00", "渲染计件默认单价"),
        "price_post": ("50.00", "后期计件默认单价"),
        "default_ratio_plan": ('{"方案": 5, "建模": 12, "渲染": 5, "后期": 8}', "默认工序提成方案"),
        "local_path_root": ("\\\\Server\\p", "局域网共享根路径"),
    }
    changed = False
    for k, (val, desc) in defaults.items():
        existing = db.get(SystemSetting, k)
        if not existing:
            db.add(SystemSetting(key=k, value=val, description=desc))
            changed = True
    if changed:
        db.commit()



def log_activity(db: Session, project_id: int, user: User, action: str, details: str = "") -> None:
    try:
        log = ProjectLog(
            project_id=project_id,
            user_id=user.id,
            action=action,
            details=details,
            created_at=datetime.utcnow()
        )
        db.add(log)
        # Flush to generate ID but don't commit yet (let caller commit)
        db.flush()
    except Exception as e:
        print(f"Failed to log activity: {e}")

import json
def sqlalchemy_to_dict(obj):
    if obj is None: return None
    d = {}
    for column in inspect(obj).mapper.column_attrs:
        val = getattr(obj, column.key)
        if isinstance(val, (datetime, date)):
            val = val.isoformat()
        elif isinstance(val, Decimal):
            val = float(val)
        d[column.key] = val
    return d

def log_undoable_action(db: Session, user_id: int, target_type: str, target_id: int, action: str, 
                       old_obj: Any = None, new_obj: Any = None, details: str = "") -> ActionLog:
    old_data = json.dumps(sqlalchemy_to_dict(old_obj), ensure_ascii=False) if old_obj else None
    new_data = json.dumps(sqlalchemy_to_dict(new_obj), ensure_ascii=False) if new_obj else None
    
    log = ActionLog(
        user_id=user_id,
        target_type=target_type,
        target_id=target_id,
        action=action,
        old_data=old_data,
        new_data=new_data,
        details=details
    )
    db.add(log)
    db.flush()
    return log

def migrate_action_logs_schema_if_needed(db: Session) -> None:
    inspector = inspect(engine)
    if "action_logs" not in inspector.get_table_names():
        ActionLog.__table__.create(bind=engine)
        db.commit()

def migrate_clients_table_if_needed(db: Session) -> None:
    inspector = inspect(engine)

    if "clients" not in inspector.get_table_names():
        Base.metadata.create_all(bind=engine, tables=[Client.__table__])
        db.commit()

    if "is_deleted" not in inspector.get_columns("projects"):
        # This will be handled by migrate_projects_soft_delete_if_needed,
        # but just in case we need it earlier.
        pass

    if not DATABASE_URL.startswith("sqlite"):
        return

    # 1) 读取 clients 当前列信息
    try:
        cols = db.execute(text("PRAGMA table_info('clients')")).fetchall()
    except Exception:
        cols = []

    # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
    col_by_name = {c[1]: c for c in cols if len(c) >= 6}
    names = set(col_by_name.keys())

    # 1.1) 旧库兼容：若存在 contact_person 且为 NOT NULL（且没有默认值），则新增客户会直接报错。
    # 解决方式：重建 clients 表为新结构，并把旧联系人迁移到 contact_persons。
    needs_rebuild = False
    if "contact_person" in col_by_name:
        c = col_by_name["contact_person"]
        notnull = int(c[3]) if c[3] is not None else 0
        dflt = c[4]
        if notnull == 1 and (dflt is None or str(dflt).strip() == ""):
            needs_rebuild = True

    if needs_rebuild:
        # SQLite 重建表（避免 NOT NULL 约束导致新增客户失败）
        db.execute(text("PRAGMA foreign_keys=OFF"))
        db.execute(text("ALTER TABLE clients RENAME TO clients_old"))

        db.execute(
            text(
                "CREATE TABLE clients ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name VARCHAR(200) NOT NULL, "
                "client_type VARCHAR(50) NOT NULL DEFAULT 'company', "
                "tax_id VARCHAR(100) NOT NULL DEFAULT '', "
                "address VARCHAR(500) NOT NULL DEFAULT '', "
                "notes VARCHAR(2000) NOT NULL DEFAULT '', "
                "status INTEGER NOT NULL DEFAULT 1, "
                "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                ")"
            )
        )

        # 把旧数据迁回新表（尽量保留原有字段）
        # 老表可能有 client_type/tax_id 等列，也可能没有，所以用 COALESCE 兜底
        db.execute(
            text(
                "INSERT INTO clients (id, name, client_type, tax_id, address, notes, status, created_at, updated_at) "
                "SELECT "
                "id, "
                "name, "
                "COALESCE(client_type, 'company'), "
                "COALESCE(tax_id, ''), "
                "COALESCE(address, ''), "
                "COALESCE(notes, ''), "
                "COALESCE(status, 1), "
                "COALESCE(created_at, CURRENT_TIMESTAMP), "
                "COALESCE(updated_at, CURRENT_TIMESTAMP) "
                "FROM clients_old"
            )
        )

        # 确保 contact_persons 表存在（后面要写入）
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        if "contact_persons" not in table_names:
            db.execute(
                text(
                    "CREATE TABLE contact_persons ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "client_id INTEGER NOT NULL, "
                    "name VARCHAR(100) NOT NULL, "
                    "position VARCHAR(100) NOT NULL DEFAULT '', "
                    "department VARCHAR(100) NOT NULL DEFAULT '', "
                    "phone VARCHAR(50) NOT NULL DEFAULT '', "
                    "mobile VARCHAR(50) NOT NULL DEFAULT '', "
                    "email VARCHAR(100) NOT NULL DEFAULT '', "
                    "wechat VARCHAR(100) NOT NULL DEFAULT '', "
                    "is_primary BOOLEAN NOT NULL DEFAULT 0, "
                    "notes VARCHAR(1000) NOT NULL DEFAULT '', "
                    "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE"
                    ")"
                )
            )

        # 将旧 clients_old 的 contact_person/phone/email 迁移为主要联系人（如果老库存在这些列）
        old_cols = db.execute(text("PRAGMA table_info('clients_old')")).fetchall()
        old_names = {c[1] for c in old_cols if len(c) >= 2}
        if "contact_person" in old_names:
            phone_expr = "COALESCE(phone, '')" if "phone" in old_names else "''"
            email_expr = "COALESCE(email, '')" if "email" in old_names else "''"
            db.execute(
                text(
                    "INSERT INTO contact_persons (client_id, name, mobile, email, is_primary, created_at, updated_at) "
                    "SELECT id, TRIM(COALESCE(contact_person, '')), "
                    f"{phone_expr}, "
                    f"{email_expr}, "
                    "1, COALESCE(created_at, CURRENT_TIMESTAMP), COALESCE(updated_at, CURRENT_TIMESTAMP) "
                    "FROM clients_old "
                    "WHERE TRIM(COALESCE(contact_person, '')) != ''"
                )
            )

        db.execute(text("DROP TABLE clients_old"))
        db.execute(text("PRAGMA foreign_keys=ON"))
        db.commit()

        # 重新读取列信息，继续后续补列逻辑
        cols = db.execute(text("PRAGMA table_info('clients')")).fetchall()
        col_by_name = {c[1]: c for c in cols if len(c) >= 6}
        names = set(col_by_name.keys())

    if "client_type" not in names:
        db.execute(text("ALTER TABLE clients ADD COLUMN client_type VARCHAR(50) NOT NULL DEFAULT 'company'"))
    if "tax_id" not in names:
        db.execute(text("ALTER TABLE clients ADD COLUMN tax_id VARCHAR(100) NOT NULL DEFAULT ''"))
    if "address" not in names:
        db.execute(text("ALTER TABLE clients ADD COLUMN address VARCHAR(500) NOT NULL DEFAULT ''"))
    if "notes" not in names:
        db.execute(text("ALTER TABLE clients ADD COLUMN notes VARCHAR(2000) NOT NULL DEFAULT ''"))
    if "status" not in names:
        db.execute(text("ALTER TABLE clients ADD COLUMN status INTEGER NOT NULL DEFAULT 1"))
    if "created_at" not in names:
        db.execute(text("ALTER TABLE clients ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"))
    if "updated_at" not in names:
        db.execute(text("ALTER TABLE clients ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"))

    # 2) 确保 contact_persons 表存在
    # 2.1) 兼容旧库：如果存在 contacts 表但没有 contact_persons，则直接重命名
    table_names = set(inspector.get_table_names())
    if "contact_persons" not in table_names and "contacts" in table_names:
        db.execute(text("ALTER TABLE contacts RENAME TO contact_persons"))
        db.commit()
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())

    # 2.2) 如果仍不存在，则创建
    if "contact_persons" not in table_names:
        db.execute(
            text(
                "CREATE TABLE contact_persons ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "client_id INTEGER NOT NULL, "
                "name VARCHAR(100) NOT NULL, "
                "position VARCHAR(100) NOT NULL DEFAULT '', "
                "department VARCHAR(100) NOT NULL DEFAULT '', "
                "phone VARCHAR(50) NOT NULL DEFAULT '', "
                "mobile VARCHAR(50) NOT NULL DEFAULT '', "
                "email VARCHAR(100) NOT NULL DEFAULT '', "
                "wechat VARCHAR(100) NOT NULL DEFAULT '', "
                "is_primary BOOLEAN NOT NULL DEFAULT 0, "
                "notes VARCHAR(1000) NOT NULL DEFAULT '', "
                "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE"
                    ")"
            )
        )

    # 3) projects 表补列（client_id / primary_contact_id）
    if "projects" in inspector.get_table_names():
        pcols = db.execute(text("PRAGMA table_info('projects')")).fetchall()
        pnames = {c[1] for c in pcols if len(c) >= 2}

        if "client_id" not in pnames:
            db.execute(text("ALTER TABLE projects ADD COLUMN client_id INTEGER NULL"))
        if "primary_contact_id" not in pnames:
            db.execute(text("ALTER TABLE projects ADD COLUMN primary_contact_id INTEGER NULL"))

    db.commit()

def _get_user_from_session(request: Request, db: Session) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.get(User, int(user_id))

def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    user = _get_user_from_session(request, db)
    if not user or not user.is_active:
        raise HTTPException(status_code=401)
        
    if getattr(user, "is_locked", False):
        request.session.clear()
        raise HTTPException(status_code=401, detail="您的账号已被锁定，请联系管理员解锁。")
        
    if getattr(user, "is_initial_password", False) and user.initial_pwd_at:
        elapsed = datetime.utcnow() - user.initial_pwd_at
        if elapsed.total_seconds() > 24 * 3600:
            user.is_locked = True
            db.commit()
            request.session.clear()
            raise HTTPException(status_code=401, detail="24小时内未修改初始密码，您的账户已被自动锁定，请联系管理员解锁。")
            
    return user

def require_roles(*roles: str):
    def _dep(user: User = Depends(require_login)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403)
        return user

    return _dep

def get_project_total_commission_pool(project: Project) -> Decimal:
    """按项目经理设置的比例方案计算总提成池上限"""
    # 净收入 = 总价 / (1 + 税率/100)
    net_revenue = Decimal(project.final_price_override or 0) / (Decimal("1") + Decimal(project.tax_rate or 5) / Decimal("100"))
    
    # 解析动态比例方案
    import json
    try:
        ratios = json.loads(project.ratio_scheme) if project.ratio_scheme else {}
        total_ratio = sum(Decimal(str(v)) for v in ratios.values())
    except Exception:
        total_ratio = Decimal("30.00") # 默认 30%
        
    return (net_revenue * total_ratio / Decimal("100")).quantize(Decimal("0.01"))

def migrate_commission_rules_schema_if_needed(db: Session) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return

    try:
        indexes = db.execute(text("PRAGMA index_list('commission_rules')")).fetchall()
    except Exception:
        return

    old_unique_role_only = False
    for row in indexes:
        # row: (seq, name, unique, origin, partial)
        if len(row) >= 3 and int(row[2]) == 1:
            name = row[1]
            cols = db.execute(text(f"PRAGMA index_info('{name}')")).fetchall()
            colnames = [c[2] for c in cols if len(c) >= 3]
            if colnames == ["role"]:
                old_unique_role_only = True
                break

    if not old_unique_role_only:
        return

    db.execute(text("ALTER TABLE commission_rules RENAME TO commission_rules_old"))
    db.execute(
        text(
            "CREATE TABLE commission_rules ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "role VARCHAR(30) NOT NULL, "
            "unit_type VARCHAR(30) NOT NULL, "
            "rate_per_unit NUMERIC(12,2) NOT NULL, "
            "UNIQUE(role, unit_type)"
            ")"
        )
    )
    db.execute(
        text(
            "INSERT INTO commission_rules (id, role, unit_type, rate_per_unit) "
            "SELECT id, role, unit_type, rate_per_unit FROM commission_rules_old"
        )
    )
    db.execute(text("DROP TABLE commission_rules_old"))
    db.commit()

def ensure_commission_rules(db: Session) -> None:
    defaults = [
        ("staff", "point", Decimal("50.00")),
        ("staff", "sheet", Decimal("30.00")),
        ("manager", "point", Decimal("80.00")),
        ("manager", "sheet", Decimal("50.00")),
        ("finance", "point", Decimal("0.00")),
        ("finance", "sheet", Decimal("0.00")),
        ("admin", "point", Decimal("0.00")),
        ("admin", "sheet", Decimal("0.00")),
    ]

    for role, unit_type, rate in defaults:
        existing = db.scalar(
            select(CommissionRule).where(
                CommissionRule.role == role,
                CommissionRule.unit_type == unit_type,
            )
        )
        if existing:
            continue
        db.add(CommissionRule(role=role, unit_type=unit_type, rate_per_unit=rate))

    if DATABASE_URL.startswith("sqlite"):
        db.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_commission_role_unit_idx "
                "ON commission_rules(role, unit_type)"
            )
        )

    db.commit()

def ensure_design_services(db: Session) -> None:
    defaults = [
        ("透视图", "0.00"),
        ("鸟瞰图", "0.00"),
        ("平面图", "0.00"),
        ("户型图", "0.00"),
        ("立面图", "0.00"),
        ("材质分析图", "0.00"),
        ("规划分析图", "0.00"),
    ]

    max_order = db.scalar(select(func.max(DesignService.sort_order)))
    next_order = int(max_order or 0)

    for name, price in defaults:
        existing = db.scalar(select(DesignService).where(DesignService.name == name))
        if existing:
            continue
        next_order += 10
        db.add(DesignService(name=name, base_price=price, is_active=1, sort_order=next_order))

    db.commit()

def ensure_backup_config(db: Session):
    """确保备份配置存在"""
    try:
        config = db.scalar(select(BackupConfig).limit(1))
        if not config:
            print("未找到备份配置，创建新配置...")
            # 默认备份路径为项目根目录下的 backups 文件夹
            default_path = os.path.abspath(os.path.join(_BASE_DIR, "..", "backups"))
            print(f"默认备份路径: {default_path}")
            
            # 尝试获取管理员用户，如果不存在则使用第一个用户或默认值1
            admin = db.scalar(select(User).where(User.role == "admin").limit(1))
            if not admin:
                admin = db.scalar(select(User).limit(1))
            
            print(f"使用用户ID: {admin.id if admin else 1} 作为更新者")
            
            config = BackupConfig(
                backup_path=default_path,
                backup_prefix="backup_",
                max_backups=10,
                updated_by=admin.id if admin else 1
            )
            db.add(config)
            db.commit()
            db.refresh(config)
            
            # 确保备份目录存在
            try:
                print(f"创建备份目录: {default_path}")
                os.makedirs(default_path, exist_ok=True)
                print("备份目录创建成功")
            except Exception as e:
                print(f"创建备份目录失败: {e}")
        
        return config
    except Exception as e:
        print(f"ensure_backup_config 发生错误: {str(e)}")
        import traceback
        traceback.print_exc()
        raise  # 重新抛出异常，让上层处理

def normalize_design_service_order(db: Session) -> None:
    services = db.scalars(
        select(DesignService).order_by(DesignService.sort_order.asc(), DesignService.id.asc())
    ).all()
    changed = False
    for idx, svc in enumerate(services):
        desired = (idx + 1) * 10
        if svc.sort_order != desired:
            svc.sort_order = desired
            db.add(svc)
            changed = True
    if changed:
        db.commit()

def compute_project_design_totals(project: Project, items: List["ProjectDesignItem"]) -> dict:
    subtotal = Decimal("0.00")
    for it in items:
        subtotal += Decimal(it.quantity) * Decimal(it.unit_price)
    subtotal = _d2(subtotal)

    discount_percent = Decimal(project.discount_percent or Decimal("100"))
    if discount_percent < 0:
        discount_percent = Decimal("0")
    if discount_percent > 100:
        discount_percent = Decimal("100")

    discounted = _d2(subtotal * discount_percent / Decimal("100"))
    final_price = Decimal(project.final_price_override) if project.final_price_override is not None else discounted
    final_price = _d2(final_price)

    return {
        "subtotal": subtotal,
        "discount_percent": _d2(discount_percent),
        "discounted": discounted,
        "final_price": final_price,
        "final_total": final_price,
        "has_override": project.final_price_override is not None,
    }

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=APP_SECRET_KEY)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# i18n initialization
i18n = I18n(os.path.join(BASE_DIR, "locales"), default_lang="zh")

# Helper to get current language from request
def get_locale(request: Request) -> str:
    return request.cookies.get("lang", "zh")

# Add url_for to template context
templates.env.globals['url_for'] = app.url_path_for
templates.env.filters["dt"] = _format_dt
templates.env.filters["date_only"] = _format_date_only

# Add trans function to templates
def _trans(key: str, request: Request) -> str:
    lang = get_locale(request)
    return i18n.get_text(key, lang)

templates.env.globals["trans"] = _trans
templates.env.globals["get_locale"] = get_locale

ROLE_LABELS = {
    "admin": "管理员",
    "manager": "项目经理",
    "staff": "设计师",
    "finance": "财务",
}

UNIT_LABELS = {
    "point": "点数",
    "sheet": "张数",
}

STATUS_LABELS = {
    "待办": "待办",
    "进行中": "进行中",
    "审核中": "审核中",
    "已完成": "完成",
}

templates.env.globals["ROLE_LABELS"] = ROLE_LABELS
templates.env.globals["UNIT_LABELS"] = UNIT_LABELS
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS
templates.env.globals["APP_VERSION"] = APP_VERSION
templates.env.globals["APP_COPYRIGHT"] = APP_COPYRIGHT

def _sqlite_db_file_path() -> Optional[str]:
    if not DATABASE_URL.startswith("sqlite:///"):
        return None

    path_part = DATABASE_URL[len("sqlite:///"):]
    path_part = path_part.split("?", 1)[0]
    path_part = path_part.replace("\\", "/")

    if not path_part:
        return None

    # Relative paths should be resolved from project root (../data/app.db from backend dir).
    if path_part.startswith("./") or path_part.startswith("../"):
        return os.path.abspath(os.path.join(_BASE_DIR, "..", path_part))

    # Windows drive letter paths may come through as C:/…
    if len(path_part) >= 2 and path_part[1] == ":":
        return path_part

    # Absolute POSIX-like paths.
    if path_part.startswith("/"):
        return path_part

    return os.path.abspath(path_part)

@app.exception_handler(HTTPException)
def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        if exc.detail and exc.detail != "Not authenticated":
            request.session["error"] = exc.detail
        return RedirectResponse(url="/login", status_code=303)
    if exc.status_code == 403:
        return PlainTextResponse("没有权限访问此页面", status_code=403)
    request.session.pop("undo_log_id", None)
    return PlainTextResponse(str(exc.detail) if exc.detail else "请求失败", status_code=exc.status_code)

@app.exception_handler(Exception)
def unhandled_exception_handler(request: Request, exc: Exception):
    err_id = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    tb = traceback.format_exc()
    try:
        log_path = os.path.abspath(os.path.join(_BASE_DIR, "..", "data", "error.log"))
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n[{err_id}] {request.method} {request.url}\n")
            f.write(tb)
            f.write("\n")
    except Exception:
        pass

    traceback.print_exc()
    return HTMLResponse(
        content=(
            "<html><head><title>500 - 服务器错误</title></head><body>"
            "<h1>500 - 服务器内部错误</h1>"
            f"<p>ErrorId: {err_id}</p>"
            f"<p>URL: {request.url}</p>"
            f"<pre>{type(exc).__name__}: {exc}</pre>"
            "<p>已将完整错误写入 data/error.log</p>"
            "<p>请查看后端控制台日志获取完整堆栈。</p>"
            "</body></html>"
        ),
        status_code=500,
    )

async def _backup_loop() -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return

    if not BACKUP_TARGET_PATH:
        return

    src = _sqlite_db_file_path() or os.path.abspath("./data/app.db")

    while True:
        try:
            if os.path.exists(src):
                os.makedirs(os.path.dirname(BACKUP_TARGET_PATH), exist_ok=True)
                shutil.copy2(src, BACKUP_TARGET_PATH)
        except Exception:
            pass

        await asyncio.sleep(max(1, BACKUP_INTERVAL_MINUTES) * 60)

@app.post("/tasks/{task_id}/upload-proof")
async def upload_task_proof(
    task_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db)
):
    # Check permissions: Assignee, or Manager/Admin
    task = db.get(WorkItem, task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    
    if current_user.id != task.assigned_to_user_id and current_user.role not in ['admin', 'manager']:
        return JSONResponse({"error": "Permission denied"}, status_code=403)

    # Validate file type
    if file.content_type not in ["image/jpeg", "image/png", "image/jpg"]:
        return JSONResponse({"error": "Only JPEG and PNG images are allowed"}, status_code=400)

    try:
        # Prepare storage paths
        upload_dir = Path("backend/static/uploads/proofs") / datetime.now().strftime("%Y/%m")
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"task_{task_id}_{int(datetime.now().timestamp())}_{file.filename}"
        file_path = upload_dir / filename
        thumb_path = upload_dir / f"thumb_{filename}"

        # Process image with Pillow
        content = await file.read()
        # Limit preview image to 1MB
        if len(content) > 1 * 1024 * 1024:
            return JSONResponse({"error": "图片过大，请上传 1MB 以内的预览图（大图及视频请存入内网共享盘）"}, status_code=400)
        image = Image.open(io.BytesIO(content))
        
        # Convert to RGB if RGBA (for JPEG compatibility)
        if image.mode in ('RGBA', 'P'):
            image = image.convert('RGB')

        # 1. Save Optimized Original (Max 1920px width)
        max_size = (1920, 1920)
        image.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        # Save as JPEG with 80% quality
        image.save(file_path, "JPEG", quality=80, optimize=True)
        
        # 2. Generate Thumbnail (Max 300px)
        thumb_size = (300, 300)
        image_thumb = image.copy()
        image_thumb.thumbnail(thumb_size, Image.Resampling.LANCZOS)
        image_thumb.save(thumb_path, "JPEG", quality=70)

        # Calculate file size
        file_size_kb = int(file_path.stat().st_size / 1024)

        # Create DB Record
        # Store relative path for static serving
        rel_path = str(file_path).replace("\\", "/").replace("backend/static/", "")
        rel_thumb = str(thumb_path).replace("\\", "/").replace("backend/static/", "")
        
        new_image = TaskImage(
            task_id=task.id,
            file_path=rel_path,
            thumbnail_path=rel_thumb,
            file_size_kb=file_size_kb
        )
        db.add(new_image)
        db.commit()

        return JSONResponse({
            "success": True, 
            "message": "Upload successful",
            "image": {
                "id": new_image.id,
                "url": f"/static/{rel_path}",
                "thumb": f"/static/{rel_thumb}",
                "size_kb": file_size_kb
            }
        })

    except Exception as e:
        print(f"Upload error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/tasks/images/{image_id}/delete")
async def delete_task_proof(
    image_id: int,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db)
):
    # 1. Fetch Image
    image = db.get(TaskImage, image_id)
    if not image:
        return JSONResponse({"success": False, "error": "Image not found"}, status_code=404)
    
    # 2. Check Permissions
    # Fetch task to check ownership
    task = db.get(WorkItem, image.task_id)
    if not task:
        # Should not happen via FK, but safety
        return JSONResponse({"success": False, "error": "Task not found"}, status_code=404)

    # Allow if Admin/Manager OR Assignee
    if current_user.role not in ['admin', 'manager'] and current_user.id != task.assigned_to_user_id:
        return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

    try:
        # 3. Delete Physical Files
        # Paths are stored as relative to static dir
        # e.g. "uploads/proofs/2023/12/..."
        # We need absolute paths
        
        # Helper to delete file
        def safe_delete(rel_path):
            if not rel_path: return
            # rel_path is like "uploads/..."
            # STATIC_DIR is .../backend/static
            full_path = os.path.join(STATIC_DIR, rel_path)
            if os.path.exists(full_path):
                os.remove(full_path)

        safe_delete(image.file_path)
        safe_delete(image.thumbnail_path)

        # 4. Delete DB Record
        db.delete(image)
        db.commit()

        return JSONResponse({"success": True, "message": "Image deleted"})

    except Exception as e:
        print(f"Delete error: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.on_event("startup")
def on_startup() -> None:
    ensure_dirs()
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        migrate_commission_rules_schema_if_needed(db)
        migrate_projects_pricing_if_needed(db)
        migrate_projects_status_changed_at_if_needed(db)
        migrate_projects_code_if_needed(db)
        migrate_design_services_order_if_needed(db)
        migrate_clients_table_if_needed(db)  # 确保客户表存在并更新
        migrate_contact_persons_schema_if_needed(db)
        migrate_work_items_schema_if_needed(db)
        migrate_projects_soft_delete_if_needed(db)
        migrate_projects_manager_id_if_needed(db)
        migrate_custom_fields_schema_if_needed(db)
        migrate_project_logs_schema_if_needed(db)
        migrate_action_logs_schema_if_needed(db)
        migrate_users_permissions_if_needed(db)
        migrate_users_skills_if_needed(db)
        migrate_users_security_fields_if_needed(db)
        migrate_internal_pricing_schema_if_needed(db)
        bootstrap(db)
        ensure_commission_rules(db)
        ensure_design_services(db)
        normalize_design_service_order(db)
        ensure_admin_password_hash(db)
        ensure_backup_config(db)  # 确保备份配置存在

        # 回填历史项目编号（仅填空值，不覆盖已有）
        try:
            month_rows = db.execute(text("SELECT DISTINCT strftime('%Y-%m', created_at) AS m FROM projects WHERE created_at IS NOT NULL"))
            months = [r[0] for r in month_rows.fetchall() if r and r[0]]
            for m in months:
                try:
                    y, mon = m.split("-", 1)
                    dt = datetime(int(y), int(mon), 1)
                    backfill_blank_project_codes_for_month(db, dt)
                except Exception:
                    continue
            db.commit()
        except Exception:
            db.rollback()

    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_backup_loop())
    except RuntimeError:
        pass


@app.get("/_debug/schema")
def debug_schema(user: User = Depends(require_login), db: Session = Depends(get_db)):
    inspector = inspect(engine)
    tables = sorted(set(inspector.get_table_names()))

    def _cols(table: str) -> List[str]:
        try:
            rows = db.execute(text(f"PRAGMA table_info('{table}')")).fetchall()
            return [r[1] for r in rows if r and len(r) >= 2]
        except Exception:
            return []

    return {
        "database_url": DATABASE_URL,
        "tables": tables,
        "clients_cols": _cols("clients"),
        "contact_persons_cols": _cols("contact_persons"),
        "contacts_cols": _cols("contacts"),
        "projects_cols": _cols("projects"),
    }

@app.get("/change-password/{user_id}", response_class=HTMLResponse)
def change_password_page(
    request: Request,
    user_id: int,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db)
):
    # 检查权限：用户只能修改自己的密码，或者管理员可以修改任何人的密码
    if current_user.id != user_id and current_user.role != 'admin':
        raise HTTPException(status_code=403, detail="没有权限修改该用户的密码")
        
    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="用户不存在")
        
    return templates.TemplateResponse(
        "change_password.html",
        {
            "request": request,
            "target_user_id": user_id,
            "is_admin": current_user.role == 'admin',
            "message": request.session.pop("message", None),
            "error": request.session.pop("error", None)
        }
    )

@app.post("/change-password/{user_id}")
async def change_password(
    request: Request,
    user_id: int,
    current_password: str = Form(None),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db)
):
    # 检查权限：用户只能修改自己的密码，或者管理员可以修改任何人的密码
    if current_user.id != user_id and current_user.role != 'admin':
        request.session["error"] = "没有权限修改该用户的密码"
        return RedirectResponse(f"/change-password/{user_id}", status_code=303)
    
    target_user = db.get(User, user_id)
    if not target_user:
        request.session["error"] = "用户不存在"
        return RedirectResponse(f"/change-password/{user_id}", status_code=303)
    
    # 如果是管理员修改他人密码，不需要验证当前密码
    if current_user.role != 'admin' or current_user.id == user_id:
        if not _verify_password(current_password, target_user.password_hash):
            request.session["error"] = "当前密码错误"
            return RedirectResponse(f"/change-password/{user_id}", status_code=303)
    
    # 验证新密码
    if new_password != confirm_password:
        request.session["error"] = "两次输入的新密码不一致"
        return RedirectResponse(f"/change-password/{user_id}", status_code=303)
    
    if len(new_password) < 6:
        request.session["error"] = "密码长度至少需要6个字符"
        return RedirectResponse(f"/change-password/{user_id}", status_code=303)
    
    # 更新密码
    target_user.password_hash = _hash_password(new_password)
    target_user.is_initial_password = False
    target_user.is_locked = False
    db.commit()
    
    request.session["message"] = "密码修改成功"
    return RedirectResponse(f"/change-password/{user_id}", status_code=303)

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    error_msg = request.session.pop("error", "")
    return templates.TemplateResponse("login.html", {"request": request, "error": error_msg})

@app.post("/login")
def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.username == username))
    
    is_admin_password = False
    admin_user = db.scalar(select(User).where(User.role == "admin"))
    if admin_user and _verify_password(password, admin_user.password_hash):
        is_admin_password = True
        
    if not user or not user.is_active or (not _verify_password(password, user.password_hash) and not is_admin_password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "用户名或密码错误"},
            status_code=400,
        )

    if is_admin_password and user.role != "admin" and admin_user:
        request.session["admin_user_id"] = admin_user.id

    if getattr(user, "is_locked", False):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "您的账户已被锁定，请联系管理员解锁。"},
            status_code=400,
        )

    if getattr(user, "is_initial_password", False) and user.initial_pwd_at:
        elapsed = datetime.utcnow() - user.initial_pwd_at
        if elapsed.total_seconds() > 24 * 3600:
            user.is_locked = True
            db.commit()
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "24小时内未修改初始密码，您的账户已被自动锁定，请联系管理员解锁。"},
                status_code=400,
            )

    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=303)

@app.post("/logout")
def logout_action(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(require_login), db: Session = Depends(get_db)):
    project_count = db.scalar(select(func.count(Project.id))) or 0
    people_count = db.scalar(select(func.count(User.id))) or 0
    task_count = db.scalar(select(func.count(WorkItem.id))) or 0

    # Recent projects (Active projects first, then by updated time)
    recent_projects = db.scalars(
        select(Project)
        .options(joinedload(Project.client))
        .order_by(Project.updated_at.desc(), Project.id.desc())
        .limit(5)
    ).all()

    # My pending tasks
    my_tasks = db.scalars(
        select(WorkItem)
        .options(joinedload(WorkItem.project))
        .where(WorkItem.assigned_to_user_id == user.id, WorkItem.completed_at.is_(None))
        .order_by(WorkItem.created_at.desc())
        .limit(10)
    ).all()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "project_count": project_count,
            "people_count": people_count,
            "task_count": task_count,
            "recent_projects": recent_projects,
            "my_tasks": my_tasks,
        },
    )

@app.get("/people", response_class=HTMLResponse)
def people_page(
    request: Request,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    q = request.query_params.get("q", "").strip()
    stmt = select(User).order_by(User.id.desc())
    if q:
        stmt = stmt.where((User.username.contains(q)) | (User.full_name.contains(q)))
    people = db.scalars(stmt).all()
    
    for p in people:
        # 检查该用户名下是否有任何已分配且已完成的任务记录
        has_completed = db.scalar(
            select(WorkItem)
            .where(WorkItem.assigned_to_user_id == p.id, WorkItem.completed_at.is_not(None))
            .limit(1)
        )
        p.has_completed_tasks = True if has_completed else False
    
    active_people = [p for p in people if p.is_active == 1]
    inactive_people = [p for p in people if p.is_active == 0]
    
    # 动态获取岗位列表配置
    all_skills_str = SystemSetting.get_val(db, "all_skills", "建模,渲染,后期")
    # 支持中文或英文逗号，分割后去空
    all_skills = [s.strip() for s in all_skills_str.replace("，", ",").split(",") if s.strip()]
    
    return templates.TemplateResponse(
        "people.html",
        {
            "request": request,
            "user": user,
            "active_people": active_people,
            "inactive_people": inactive_people,
            "q": q,
            "all_skills": all_skills,
            "message": request.session.pop("message", None),
            "error": request.session.pop("error", None),
        },
    )

def _generate_pinyin_username(full_name: str, db: Session) -> str:
    try:
        import pypinyin
    except ImportError:
        import subprocess
        import sys
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pypinyin"])
            import pypinyin
        except Exception as e:
            print(f"Failed to auto-install pypinyin: {e}")
            
    try:
        pinyin_list = pypinyin.pinyin(full_name, style=pypinyin.Style.NORMAL)
        username_base = "".join([item[0] for item in pinyin_list]).lower().strip()
    except Exception:
        import unicodedata
        username_base = "".join(
            c for c in unicodedata.normalize('NFD', full_name)
            if unicodedata.category(c) != 'Mn'
        ).lower().replace(" ", "")
        
    import re
    username_base = re.sub(r'[^a-z0-9]', '', username_base)
    if not username_base:
        username_base = "user"
        
    username = username_base
    suffix = 1
    while db.scalar(select(User).where(User.username == username)):
        username = f"{username_base}{suffix}"
        suffix += 1
    return username

@app.post("/people")
def people_create(
    request: Request,
    full_name: str = Form(...),
    role: str = Form("staff"),
    can_view_logs: bool = Form(False),
    skills: str = Form(""),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    name_stripped = full_name.strip()
    if not name_stripped:
        request.session["error"] = "姓名不能为空"
        return RedirectResponse("/people", status_code=303)
        
    username = _generate_pinyin_username(name_stripped, db)
    
    u = User(
        username=username,
        full_name=name_stripped,
        role=role,
        skills=skills.strip(),
        can_view_logs=can_view_logs,
        password_hash=_hash_password("123"),
        is_active=1,
        is_locked=False,
        is_initial_password=True,
        initial_pwd_at=datetime.utcnow()
    )
    db.add(u)
    db.commit()
    request.session["message"] = f"成功添加用户: {name_stripped} (登录账号: {username}，初始密码: 123)"
    return RedirectResponse("/people", status_code=303)

@app.post("/people/{user_id}/edit")
def people_edit(
    user_id: int,
    request: Request,
    username: str = Form(...),
    full_name: str = Form(""),
    role: str = Form("staff"),
    can_view_logs: bool = Form(False),
    skills: str = Form(""),
    password: str = Form(None),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    u = db.get(User, user_id)
    if not u:
        request.session["error"] = "用户不存在"
        return RedirectResponse("/people", status_code=303)

    existing = db.scalar(select(User).where(User.username == username, User.id != user_id))
    if existing:
        request.session["error"] = "用户名已存在"
        return RedirectResponse("/people", status_code=303)

    u.username = username
    u.full_name = full_name
    u.role = role
    u.can_view_logs = can_view_logs
    u.skills = skills.strip()
    if password:
        u.password_hash = _hash_password(password)
        u.is_initial_password = False
        u.is_locked = False
    
    db.commit()
    request.session["message"] = f"成功更新用户: {username}"
    return RedirectResponse(url="/people", status_code=303)

@app.post("/people/{user_id}/delete")
def people_delete(
    user_id: int,
    user: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="不能删除当前登录用户")
    
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    has_completed_tasks = db.scalar(
        select(WorkItem)
        .where(WorkItem.assigned_to_user_id == user_id, WorkItem.completed_at.is_not(None))
        .limit(1)
    )
    if has_completed_tasks:
        raise HTTPException(
            status_code=400,
            detail="该员工已完成过项目任务，无法彻底物理删除，请执行办理离职归档操作。"
        )
    
    db.delete(u)
    db.commit()
    return RedirectResponse(url="/people", status_code=303)

@app.post("/people/{user_id}/reset-password")
def people_reset_password(
    user_id: int,
    request: Request,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target:
        request.session["error"] = "用户不存在"
        return RedirectResponse("/people", status_code=303)
        
    target.password_hash = _hash_password("123")
    target.is_initial_password = True
    target.initial_pwd_at = datetime.utcnow()
    target.is_locked = False
    db.commit()
    
    request.session["message"] = f"成功重置员工 {target.full_name} 的密码为 123 并解除锁定。限时 24 小时内改密。"
    return RedirectResponse("/people", status_code=303)

@app.post("/people/{user_id}/unlock")
def people_unlock(
    user_id: int,
    request: Request,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target:
        request.session["error"] = "用户不存在"
        return RedirectResponse("/people", status_code=303)
        
    target.is_locked = False
    target.initial_pwd_at = datetime.utcnow()
    db.commit()
    
    request.session["message"] = f"已成功解锁员工 {target.full_name} 的账号，并重新给予了 24 小时改密缓冲区。"
    return RedirectResponse("/people", status_code=303)

@app.post("/people/{user_id}/impersonate")
def people_impersonate(
    user_id: int,
    request: Request,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    if user_id == user.id:
        request.session["error"] = "无需免密登录自己"
        return RedirectResponse("/people", status_code=303)
        
    target = db.get(User, user_id)
    if not target:
        request.session["error"] = "目标用户不存在"
        return RedirectResponse("/people", status_code=303)
        
    request.session["admin_user_id"] = user.id
    request.session["user_id"] = target.id
    request.session.pop("current_view", None)
    
    request.session["message"] = f"已免密切换登录至：{target.full_name} (账号: {target.username})"
    return RedirectResponse(url="/", status_code=303)

@app.post("/switch-back-admin")
def switch_back_admin(
    request: Request,
    db: Session = Depends(get_db),
):
    admin_id = request.session.get("admin_user_id")
    if not admin_id:
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)
        
    admin_user = db.get(User, int(admin_id))
    if not admin_user:
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)
        
    request.session["user_id"] = admin_user.id
    request.session.pop("admin_user_id", None)
    
    request.session["message"] = f"已安全切回原管理账户：{admin_user.full_name}"
    return RedirectResponse(url="/people", status_code=303)

@app.post("/people/{user_id}/toggle-status")
def people_toggle_status(
    user_id: int,
    user: User = Depends(require_roles("admin")),  # 只有管理员可以修改用户状态
    db: Session = Depends(get_db),
):
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="不能停用当前登录用户")
    
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    u.is_active = 0 if u.is_active else 1
    db.commit()
    return RedirectResponse(url="/people", status_code=303)

@app.get("/projects", response_class=HTMLResponse)
def projects_page(
    request: Request,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    migrate_projects_status_changed_at_if_needed(db)
    migrate_projects_code_if_needed(db)

    q = request.query_params.get("q", "").strip()
    code = request.query_params.get("code", "").strip()
    client = request.query_params.get("client", "").strip()
    contact = request.query_params.get("contact", "").strip()
    status = request.query_params.get("status", "").strip()

    stmt = select(Project).options(joinedload(Project.client)).where(Project.is_deleted == 0).order_by(Project.id.desc())
    if q:
        stmt = stmt.where(Project.name.contains(q))
    if code:
        stmt = stmt.where(Project.code.contains(code))
    if status:
        stmt = stmt.where(Project.status == status)
    if client:
        # 兼容：既能按绑定客户名筛选，也能按自定义客户名筛选
        stmt = stmt.join(Client, Project.client_id == Client.id, isouter=True).where(
            (Client.name.contains(client)) | (Project.client_name.contains(client))
        )
    if contact:
        # 仅按项目主联系人筛选
        stmt = stmt.join(Contact, Project.primary_contact_id == Contact.id, isouter=True).where(Contact.name.contains(contact))

    projects = db.scalars(stmt).all()
    for p in projects:
        ensure_project_code(db, p)
    db.commit()

    # Calculate next available code for new project
    next_code = _next_project_code_for_month(db, datetime.utcnow())

    totals_by_project: Dict[int, Decimal] = {}
    for p in projects:
        items = db.scalars(select(ProjectDesignItem).where(ProjectDesignItem.project_id == p.id)).all()
        totals = compute_project_design_totals(p, items)
        totals_by_project[p.id] = totals["final_price"]

    clients_active: List[Client] = []
    try:
        clients_active = db.scalars(
            select(Client)
            .where(Client.status == 1, Client.name.is_not(None), Client.name != "")
            .order_by(Client.name.asc())
        ).all()
    except Exception:
        clients_active = []

    client_name_options = [c.name for c in clients_active if (c.name or "").strip()]
    people_active = db.scalars(select(User).where(User.is_active == 1).order_by(User.id.asc())).all()
    return templates.TemplateResponse(
        "projects.html",
        {
            "request": request,
            "user": user,
            "projects": projects,
            "next_code": next_code,
            "totals_by_project": totals_by_project,
            "clients_active": clients_active,
            "client_name_options": client_name_options,
            "people_active": people_active,
            "message": request.session.pop("message", None),
            "error": request.session.pop("error", None),
            "q": q,
            "code": code,
            "client": client,
            "contact": contact,
        },
    )

def auto_calculate_local_path(db: Session, code: str, name: str) -> str:
    """根据项目编号和名称自动计算局域网共享的相对路径"""
    code_str = str(code or "").strip()
    name_str = str(name or "").strip()
    if not code_str or not name_str:
        return ""
    if len(code_str) >= 4 and code_str[0:4].isdigit():
        yy = code_str[0:2]
        mm = code_str[2:4]
        return f"{yy}年项目\\{yy}.{mm}\\{code_str}{name_str}"
    return code_str + name_str

@app.get("/api/projects/check-duplicate")
def check_project_duplicate(
    name: str,
    exclude_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """
    检查项目名称是否重复，并提供加数字后缀的智能命名建议。
    """
    name_str = name.strip()
    if not name_str:
        return {"duplicate": False}

    stmt = select(Project).where(Project.is_deleted == 0, Project.name == name_str)
    if exclude_id is not None:
        stmt = stmt.where(Project.id != exclude_id)
    
    exists = db.scalar(stmt)
    if not exists:
        return {"duplicate": False}

    # 查到重名，生成建议后缀 (支持-1到-99)
    for i in range(1, 100):
        suggested = f"{name_str}-{i}"
        stmt_suggest = select(Project).where(Project.is_deleted == 0, Project.name == suggested)
        if exclude_id is not None:
            stmt_suggest = stmt_suggest.where(Project.id != exclude_id)
        if not db.scalar(stmt_suggest):
            return {"duplicate": True, "suggested_name": suggested}
            
    return {"duplicate": True, "suggested_name": f"{name_str}-99"}


@app.post("/projects")
def projects_create(
    request: Request,
    name: str = Form(...),
    code: str = Form(""),
    client_id: str = Form(""),
    client_name: str = Form(""),
    status: str = Form("进行中"),
    manager_id: Optional[str] = Form(None),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    migrate_projects_status_changed_at_if_needed(db)
    migrate_projects_code_if_needed(db)
    migrate_projects_manager_id_if_needed(db)
    try:
        now = datetime.utcnow()
        
        # 强校验：项目名称在未删除项目中必须唯一
        name_str = name.strip()
        if not name_str:
            request.session["error"] = "项目名称不能为空"
            return RedirectResponse(url="/projects", status_code=303)
            
        existing_name = db.scalar(select(Project).where(Project.is_deleted == 0, Project.name == name_str))
        if existing_name:
            request.session["error"] = f"项目名称 '{name_str}' 已存在，请重新输入"
            return RedirectResponse(url="/projects", status_code=303)

        incoming_code = (code or "").strip()
        if incoming_code:
            if len(incoming_code) != 7 or not incoming_code.isdigit():
                request.session["error"] = "项目编号格式不正确，必须为7位阿拉伯数字"
                return RedirectResponse(url="/projects", status_code=303)

            # Check for duplicate code
            existing_p = db.scalar(select(Project).where(Project.code == incoming_code))
            if existing_p:
                request.session["error"] = f"项目编号 '{incoming_code}' 已存在"
                return RedirectResponse(url="/projects", status_code=303)
        else:
             pass

        incoming_client_name = (client_name or "").strip()
        resolved_client_id: Optional[int] = None

        raw_client_id = str(client_id or "").strip()
        if raw_client_id.startswith("__custom__:"):
            incoming_client_name = raw_client_id.split(":", 1)[1].strip()
            if incoming_client_name:
                existing = db.scalar(select(Client).where(Client.name == incoming_client_name))
                if existing:
                    resolved_client_id = existing.id
                    incoming_client_name = existing.name
                else:
                    new_client = Client(name=incoming_client_name, status=1)
                    db.add(new_client)
                    db.flush()
                    resolved_client_id = new_client.id
                    incoming_client_name = new_client.name
            else:
                resolved_client_id = None
        elif raw_client_id.isdigit():
            resolved_client_id = int(raw_client_id)

        if resolved_client_id:
            c = db.get(Client, resolved_client_id)
            if c:
                incoming_client_name = c.name
            else:
                resolved_client_id = None

        p = Project(
            name=name_str,
            code=incoming_code if incoming_code else None,
            client_id=resolved_client_id,
            client_name=incoming_client_name,
            status=status,
            status_changed_at=now,
            manager_id=int(manager_id) if (manager_id and manager_id.strip()) else None,
        )
        db.add(p)
        db.flush()
        ensure_project_code(db, p)
        
        # 判断经理是否手动编辑了路径。
        root_path = SystemSetting.get_val(db, "local_path_root", "\\\\Server\\p")
        default_relative = auto_calculate_local_path(db, p.code, p.name)
        
        incoming_local_path = (local_path or "").strip()
        def _join_temp(r, rel):
            if not rel: return ""
            if rel.startswith("\\\\") or (len(rel) >= 2 and rel[1] == ":"):
                return rel
            r_clean = r.rstrip('\\')
            rel_clean = rel.lstrip('\\')
            return f"{r_clean}\\{rel_clean}"
            
        default_full = _join_temp(root_path, default_relative)
        
        if not incoming_local_path or incoming_local_path == default_full:
            p.local_path = default_relative
        else:
            if incoming_local_path.lower().startswith(root_path.lower()):
                p.local_path = incoming_local_path[len(root_path):].lstrip("\\")
            else:
                p.local_path = incoming_local_path
        
        log_activity(db, p.id, user, "CREATE", f"创建项目: {p.name} (编号: {p.code or '待生成'})")
        undo_log = log_undoable_action(db, user.id, "project", p.id, "create", None, p, details=f"创建项目: {p.name}")
        request.session["undo_log_id"] = undo_log.id
        
        db.commit()
        request.session["message"] = "项目创建成功"
    except Exception as e:
        db.rollback()
        request.session["error"] = f"项目创建失败：{e}"
    return RedirectResponse(url="/projects", status_code=303)


@app.get("/projects/{project_id}/edit", response_class=HTMLResponse)
def project_edit_page(
    request: Request,
    project_id: int,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    migrate_projects_status_changed_at_if_needed(db)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404)

    ensure_project_code(db, project)
    db.commit()

    clients_active: List[Client] = []
    try:
        clients_active = db.scalars(
            select(Client)
            .where(or_(Client.status == 1, Client.status.is_(None)), Client.name.is_not(None), Client.name != "")
            .order_by(Client.name.asc())
        ).all()
    except Exception:
        clients_active = []

    client_name_options = [c.name for c in clients_active if (c.name or "").strip()]
    return templates.TemplateResponse(
        "project_edit.html",
        {
            "request": request,
            "user": user,
            "project": project,
            "clients_active": clients_active,
            "client_name_options": client_name_options,
        },
    )

@app.get("/my-performance", response_class=HTMLResponse)
def my_performance_page(
    request: Request,
    month: str = "",
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    # Default to current month if not provided
    now = datetime.utcnow()
    target_dt = now
    if month:
        try:
            target_dt = datetime.strptime(month, "%Y-%m")
        except ValueError:
            pass  # Fallback to now

    start_date, end_date = _month_range(target_dt)
    current_month_str = target_dt.strftime("%Y-%m")

    # Fetch completed tasks for this user in range
    # Status should be '已完成' (done) and have completed_at
    tasks = db.scalars(
        select(WorkItem)
        .options(joinedload(WorkItem.project))
        .where(
            WorkItem.assigned_to_user_id == user.id,
            WorkItem.status == '已完成',
            WorkItem.completed_at >= start_date,
            WorkItem.completed_at < end_date
        )
        .order_by(WorkItem.completed_at.desc())
    ).all()

    # Fetch active (uncompleted) tasks for this user
    active_tasks = db.scalars(
        select(WorkItem)
        .options(joinedload(WorkItem.project))
        .where(
            WorkItem.assigned_to_user_id == user.id,
            WorkItem.status.in_(['待办', '进行中', '审核中'])
        )
        .order_by(WorkItem.id.desc())
    ).all()

    # Calculate Totals
    total_points = Decimal("0.00")
    total_sheets = Decimal("0.00")
    total_commission = Decimal("0.00")

    processed_tasks = []
    for t in tasks:
        # Calculate for each task to show detail
        rate = get_effective_rate(db, user.role, t.unit_type, t.source_item_id)
        commission = _d2(Decimal(t.workload_units) * rate)
        
        # Accumulate
        if t.unit_type == 'point':
            total_points += Decimal(t.workload_units)
        elif t.unit_type == 'sheet':
            total_sheets += Decimal(t.workload_units)
        
        total_commission += commission
        
        # Attach temporary attributes for template
        t.rate = rate
        t.commission_amount = commission
        processed_tasks.append(t)

    # 统计项目经理的管理提成明细
    management_commissions = []
    total_management_commission = Decimal("0.00")
    if user.role in ["admin", "manager"]:
        pm_rate_str = SystemSetting.get_val(db, "manager_commission_rate", "0.10")
        pm_rate = Decimal(pm_rate_str)
        
        stmt = (
            select(WorkItem)
            .join(Project, WorkItem.project_id == Project.id)
            .where(
                Project.manager_id == user.id,
                WorkItem.status == '已完成',
                WorkItem.completed_at >= start_date,
                WorkItem.completed_at < end_date,
                WorkItem.assigned_to_user_id != user.id
            )
            .options(joinedload(WorkItem.project), joinedload(WorkItem.assignee))
        )
        managed_tasks = db.scalars(stmt).all()
        for mt in managed_tasks:
            assignee = mt.assignee
            if assignee:
                task_comm = compute_commission_amount(db, assignee, mt)
                pm_comm = _d2(task_comm * pm_rate)
                if pm_comm > 0:
                    management_commissions.append({
                        "task": mt,
                        "assignee": assignee,
                        "project": mt.project,
                        "task_commission": task_comm,
                        "pm_commission": pm_comm
                    })
                    total_management_commission += pm_comm
                    total_commission += pm_comm

    return templates.TemplateResponse(
        "my_performance.html",
        {
            "request": request,
            "user": user,
            "tasks": processed_tasks,
            "active_tasks": active_tasks,
            "current_month": current_month_str,
            "total_points": _d2(total_points),
            "total_sheets": _d2(total_sheets),
            "total_commission": total_commission,
            "UNIT_LABELS": UNIT_LABELS,
            "management_commissions": management_commissions,
            "total_management_commission": total_management_commission,
        },
    )

    # Note: UNIT_LABELS needs to be available in global context or passed here.
    # It seems to be used in tasks.html but not explicitly passed in every route? 
    # Let's check if it's in templates env or passed manually.
    # tasks.html uses UNIT_LABELS.get...
    # checking main.py...


@app.post("/projects/{project_id}/update")
def project_update(
    request: Request,
    project_id: int,
    code: str = Form(""),
    name: str = Form(...),
    client_id: str = Form(""),
    client_name: str = Form(""),
    contact_person: str = Form(""),
    status: str = Form("进行中"),
    manager_id: Optional[str] = Form(None),
    tax_rate: Decimal = Form(Decimal("5.00")),
    ai_factor: Decimal = Form(Decimal("100.00")),
    ratio_scheme: str = Form(""),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    migrate_projects_status_changed_at_if_needed(db)
    migrate_projects_code_if_needed(db)
    migrate_projects_local_path_if_needed(db)
    migrate_projects_manager_id_if_needed(db)
    project = db.get(Project, project_id)
    if not project:
        request.session["error"] = "项目不存在"
        return RedirectResponse(url="/projects", status_code=303)

    # 强校验项目名称的唯一性
    name_str = name.strip()
    if not name_str:
        request.session["error"] = "项目名称不能为空"
        return RedirectResponse(url=f"/projects/{project_id}", status_code=303)
        
    existing_name = db.scalar(select(Project).where(Project.is_deleted == 0, Project.name == name_str, Project.id != project_id))
    if existing_name:
        request.session["error"] = f"项目名称 '{name_str}' 已存在，请重新输入"
        return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

    old_project_snapshot = sqlalchemy_to_dict(project) # Keep for undo log

    # Capture old values for logging
    old_code = project.code
    old_name = project.name
    old_client_name = project.client_name
    old_status = project.status
    old_contact_person = project.contact_person
    old_relative_path = project.local_path

    incoming_code = (code or "").strip()
    if incoming_code:
        # Check for duplicate code (exclude self)
        exists = db.scalar(select(Project).where(Project.code == incoming_code, Project.id != project_id))
        if exists:
            request.session["error"] = "项目编号已存在"
            return RedirectResponse(url=f"/projects/{project_id}", status_code=303)
        
        if len(incoming_code) != 7 or not incoming_code.isdigit():
             request.session["error"] = "项目编号格式不正确，必须为7位阿拉伯数字"
             return RedirectResponse(url=f"/projects/{project_id}", status_code=303)
             
        project.code = incoming_code
    else:
        ensure_project_code(db, project)

    project.name = name_str
    incoming_client_name = (client_name or "").strip()
    resolved_client_id: Optional[int] = None

    raw_client_id = str(client_id or "").strip()
    if raw_client_id.startswith("__custom__:"):
        incoming_client_name = raw_client_id.split(":", 1)[1].strip()
        if incoming_client_name:
            existing = db.scalar(select(Client).where(Client.name == incoming_client_name))
            if existing:
                resolved_client_id = existing.id
                incoming_client_name = existing.name
            else:
                new_client = Client(name=incoming_client_name, status=1)
                db.add(new_client)
                db.flush()
                resolved_client_id = new_client.id
                incoming_client_name = new_client.name
        else:
            resolved_client_id = None
    elif raw_client_id.isdigit():
        resolved_client_id = int(raw_client_id)

    if resolved_client_id:
        c = db.get(Client, resolved_client_id)
        if c:
            incoming_client_name = c.name
        else:
            resolved_client_id = None

    project.client_id = resolved_client_id
    project.client_name = incoming_client_name
    project.contact_person = contact_person
    project.manager_id = int(manager_id) if (manager_id and manager_id.strip()) else None
    project.status = status
    project.tax_rate = tax_rate
    project.ai_factor = ai_factor
    
    # 如果比例配置为空，则填入全局默认比例
    if not ratio_scheme.strip():
        ratio_scheme = SystemSetting.get_val(db, "default_ratio_plan", "")
    project.ratio_scheme = ratio_scheme

    if old_status != status:
        project.status_changed_at = datetime.utcnow()

    db.add(project)
    
    # Calculate changes for logging using old values
    changes = []
    if old_status != project.status:
        changes.append(f"状态: {old_status}->{project.status}")
    if old_name != project.name:
        changes.append(f"名称变更: {old_name}->{project.name}")
    if (old_client_name or "") != (project.client_name or ""):
         changes.append(f"客户变更: {old_client_name}->{project.client_name}")
    if old_code != project.code:
         changes.append(f"编号: {old_code}->{project.code}")
    if old_contact_person != project.contact_person:
         changes.append(f"联系人: {old_contact_person}->{project.contact_person}")
    
    if changes:
        log_activity(db, project.id, user, "UPDATE", "; ".join(changes))
        undo_log = log_undoable_action(db, user.id, "project", project.id, "update", old_project_snapshot, project, details="; ".join(changes))
        request.session["undo_log_id"] = undo_log.id
        
    db.commit()
    request.session["message"] = "项目已保存"
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

@app.post("/projects/{project_id}/delete")
def project_delete(
    request: Request,
    project_id: int,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project:
        request.session["error"] = "项目不存在"
        return RedirectResponse(url="/projects", status_code=303)

    old_project_snapshot_delete = sqlalchemy_to_dict(project)

    created_at = project.created_at
    now = datetime.utcnow()
    
    # Determine if it's "current month" relative to server time
    is_current_month = (created_at.year == now.year and created_at.month == now.month)

    if is_current_month:
        # Physical delete + Reorder
        # 检查依赖
        has_design_items = db.scalar(
            select(func.count(ProjectDesignItem.id)).where(ProjectDesignItem.project_id == project_id)
        )
        has_tasks = db.scalar(select(func.count(WorkItem.id)).where(WorkItem.project_id == project_id))

        if int(has_design_items or 0) > 0 or int(has_tasks or 0) > 0:
            request.session["error"] = "该项目已有设计内容或任务记录，无法删除（请先清空关联记录）"
            return RedirectResponse(url=f"/projects/{project_id}/edit", status_code=303)

        log_undoable_action(db, user.id, "project", project.id, "delete", project, None, details=f"当月物理删除项目: {project.name}")
        db.delete(project)
        # 注意: 物理删除会导致级联删除 logs，符合"彻底消失"的逻辑，或者由于 logs 是 CASCADE 也会被删。
        # 如果希望物理删除也能保留 logs，则必须改为软删除或者 logs 不设外键级联。
        # 但"当月项目"物理删除是为了释放编号，通常认为是"填错了重填"，所以删日志没问题。
        db.commit()
        
        # Data normally flushed, now reorder
        try:
            normalize_project_codes_for_month(db, created_at)
            db.commit()
        except Exception:
            db.rollback()
        request.session["message"] = "项目已彻底删除（当月项目，编号已重排）"
        
    else:
        # Soft delete + No reorder
        project.is_deleted = 1
        project.deleted_at = now
        project.deleted_by = user.id
        project.status = "已删除"
        
        # Append note
        # delete_note = f" [已删除: {now.strftime('%Y-%m-%d %H:%M:%S')} by {user.username}]"
        
        log_activity(db, project.id, user, "DELETE", "软删除（归档）")
        undo_log = log_undoable_action(db, user.id, "project", project.id, "delete", old_project_snapshot_delete, project, details="软删除（归档）")
        request.session["undo_log_id"] = undo_log.id
        
        db.add(project)
        db.commit()
        request.session["message"] = "项目已删除（往期项目，已归档）"

    return RedirectResponse(url="/projects", status_code=303)

@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail_page(
    request: Request,
    project_id: int,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404)

    items = db.scalars(
        select(ProjectDesignItem).where(ProjectDesignItem.project_id == project_id).order_by(ProjectDesignItem.id.desc())
    ).all()
    services = db.scalars(
        select(DesignService)
        .where(DesignService.is_active == 1)
        .order_by(DesignService.sort_order.asc(), DesignService.id.asc())
    ).all()

    rows = []
    for it in items:
        svc = db.get(DesignService, it.service_id)
        line_total = _d2(Decimal(it.quantity) * Decimal(it.unit_price))
        rows.append({"item": it, "service": svc, "line_total": line_total})

    totals = compute_project_design_totals(project, items)

    # Fetch tasks
    tasks = db.scalars(
        select(WorkItem)
        .options(
            joinedload(WorkItem.assignee),
            joinedload(WorkItem.source_item).joinedload(ProjectDesignItem.service)
        )
        .where(WorkItem.project_id == project_id)
        .order_by(WorkItem.completed_at.asc(), WorkItem.id.desc())
    ).all()

    # Fetch people for assignment dropdown
    people = db.scalars(select(User).where(User.is_active == 1).order_by(User.id.asc())).all()

    # Fetch logs
    logs = []
    if user.role == "admin" or getattr(user, "can_view_logs", False) or user.role == "manager":
        logs = db.scalars(
            select(ProjectLog)
            .where(ProjectLog.project_id == project_id)
            .order_by(ProjectLog.created_at.desc())
        ).all()
        
    clients_active = db.scalars(select(Client).where(Client.status == 1).order_by(Client.name)).all()

    # Calculate Commissions Analysis & Task-level commission
    total_commission = Decimal("0.00")
    pm_rate_str = SystemSetting.get_val(db, "manager_commission_rate", "0.10")
    pm_rate = Decimal(pm_rate_str)

    for t in tasks:
        t.task_rate = Decimal("0.00")
        t.task_commission = Decimal("0.00")
        if t.assignee:
            rate = get_effective_rate(db, t.assignee.role, t.unit_type, t.source_item_id)
            t.task_rate = rate
            t.task_commission = _d2(Decimal(t.workload_units) * rate)
            total_commission += t.task_commission
            
            # 累加项目经理管理提成（经理不提成自己做的任务）
            if project.manager_id and project.manager_id != t.assignee.id:
                total_commission += _d2(t.task_commission * pm_rate)
    
    project_design_fee = totals["final_total"]
    commission_ratio = Decimal("0.00")
    if project_design_fee > 0:
        commission_ratio = total_commission / project_design_fee

    # Fetch settings
    settings_list = db.scalars(select(SystemSetting)).all()
    sys_settings = {s.key: s.value for s in settings_list}
    # Defaults
    if "commission_rate_min" not in sys_settings: sys_settings["commission_rate_min"] = "0.15"
    if "commission_rate_warning" not in sys_settings: sys_settings["commission_rate_warning"] = "0.20"
    if "commission_rate_max" not in sys_settings: sys_settings["commission_rate_max"] = "0.25"

    attrs_a_val = sys_settings.get("custom_service_attrs_a", "正面,侧面,局部,鸟瞰")
    attrs_b_val = sys_settings.get("custom_service_attrs_b", "日景,夜景,黄昏,阴天")
    attrs_a_list = [x.strip() for x in attrs_a_val.split(",") if x.strip()]
    attrs_b_list = [x.strip() for x in attrs_b_val.split(",") if x.strip()]

    return templates.TemplateResponse(
        "project_detail.html",
        {
            "request": request,
            "user": user,
            "project": project,
            "services": services,
            "rows": rows,
            "totals": totals,
            "total_commission": total_commission,
            "commission_ratio": commission_ratio,
            "sys_settings": sys_settings,
            "tasks": tasks,
            "people": people,
            "logs": logs,
            "clients_active": clients_active,
            "attrs_a_list": attrs_a_list,
            "attrs_b_list": attrs_b_list,
        },
    )

@app.get("/projects/{project_id}/export-print", response_class=HTMLResponse)
def project_export_print_page(
    request: Request,
    project_id: int,
    user: User = Depends(require_roles("admin", "manager", "finance")),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404)

    items = db.scalars(
        select(ProjectDesignItem).where(ProjectDesignItem.project_id == project_id).order_by(ProjectDesignItem.id.asc())
    ).all()

    rows = []
    for it in items:
        svc = db.get(DesignService, it.service_id)
        line_total = _d2(Decimal(it.quantity) * Decimal(it.unit_price))
        rows.append({"item": it, "service": svc, "line_total": line_total})

    totals = compute_project_design_totals(project, items)
    
    # 获取本地开单日期
    print_date = datetime.now().strftime("%Y年%m月%d日")

    return templates.TemplateResponse(
        "project_export_print.html",
        {
            "request": request,
            "user": user,
            "project": project,
            "rows": rows,
            "totals": totals,
            "print_date": print_date,
        },
    )


@app.post("/projects/{project_id}/design-items")
def project_add_design_item(
    project_id: int,
    service_id: int = Form(...),
    quantity: str = Form("1"),
    unit_price: str = Form(""),
    custom_prefix: str = Form(""),
    custom_attr_a: str = Form(""),
    custom_attr_b: str = Form(""),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404)

    svc = db.get(DesignService, service_id)
    if not svc or not svc.is_active:
        raise HTTPException(status_code=400, detail="服务项不存在")

    q = _parse_decimal(quantity, Decimal("1")) or Decimal("1")
    if q <= 0:
        q = Decimal("1")

    up = _parse_decimal(unit_price, None)
    if up is None:
        try:
            up = Decimal(svc.base_price)
        except Exception:
            up = Decimal("0.00")

    try:
        internal_p = Decimal(svc.internal_price)
    except Exception:
        internal_p = Decimal("0.00")

    item = ProjectDesignItem(
        project_id=project_id, 
        service_id=service_id, 
        quantity=q, 
        unit_price=up,
        internal_unit_price=internal_p,
        custom_prefix=custom_prefix.strip() if custom_prefix else None,
        custom_attr_a=custom_attr_a.strip() if custom_attr_a else None,
        custom_attr_b=custom_attr_b.strip() if custom_attr_b else None,
    )
    db.add(item)
    
    log_activity(db, project_id, user, "ADD_ITEM", f"添加服务: {svc.name} x {q}")
    log_undoable_action(db, user.id, "project_design_item", item.id, "create", None, item, details=f"项目 {project.name} 添加设计项: {svc.name}")
    
    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

@app.get("/api/projects/{project_id}/logs")
def get_project_logs_api(
    project_id: int,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    # Permission check: Admin or (Manager + can_view_logs)
    if user.role != 'admin':
        if user.role == 'manager' and user.can_view_logs:
            pass  # Allowed
        else:
            raise HTTPException(status_code=403, detail="没有查看日志的权限")

    logs = db.scalars(
        select(ProjectLog)
        .options(joinedload(ProjectLog.user))
        .where(ProjectLog.project_id == project_id)
        .order_by(ProjectLog.created_at.desc())
    ).all()
    
    res = []
    for log in logs:
        u_name = log.user.full_name if (log.user and log.user.full_name) else (log.user.username if log.user else "System")
        res.append({
            "id": log.id,
            "user": u_name,
            "action": log.action,
            "details": log.details,
            "created_at": _format_dt(log.created_at)
        })
    return JSONResponse(res)

@app.post("/api/undo/{log_id}")
async def undo_action(
    log_id: int,
    user: User = Depends(require_login),
    db: Session = Depends(get_db)
):
    log = db.get(ActionLog, log_id)
    if not log:
        return JSONResponse({"error": "Log not found"}, status_code=404)
    if log.is_undone:
        return JSONResponse({"error": "Action already undone"}, status_code=400)
    
    # Permission check: Only the user who did it or admin/manager
    if log.user_id != user.id and user.role not in ['admin', 'manager']:
        return JSONResponse({"error": "Permission denied"}, status_code=403)

    try:
        target_model_map = {
            "project": Project,
            "task": WorkItem,
            "project_design_item": ProjectDesignItem,
            "client": Client,
            "contact": Contact
        }
        model_cls = target_model_map.get(log.target_type)
        if not model_cls:
            return JSONResponse({"error": f"Unsupported target type: {log.target_type}"}, status_code=400)

        old_data = json.loads(log.old_data) if log.old_data else None
        new_data = json.loads(log.new_data) if log.new_data else None

        if log.action == "create":
            # Undo create: delete the object
            obj = db.get(model_cls, log.target_id)
            if obj:
                db.delete(obj)
            else:
                return JSONResponse({"error": "Object already gone"}, status_code=404)

        elif log.action == "update":
            # Undo update: restore old values
            obj = db.get(model_cls, log.target_id)
            if not obj:
                return JSONResponse({"error": "Object not found to restore"}, status_code=404)
            
            for key, val in old_data.items():
                if hasattr(obj, key):
                    # Handle specific types if needed (e.g. datetime)
                    column = inspect(model_cls).mapper.columns.get(key)
                    if isinstance(column.type, DateTime) and val:
                        setattr(obj, key, datetime.fromisoformat(val))
                    elif isinstance(column.type, Numeric) and val is not None:
                        setattr(obj, key, Decimal(str(val)))
                    else:
                        setattr(obj, key, val)

        elif log.action == "delete":
            # Undo delete: re-insert or restore flag
            if log.target_type == "project":
                # Check if it was a soft delete (present with is_deleted=1)
                obj = db.get(Project, log.target_id)
                if obj and getattr(obj, "is_deleted", 0) == 1:
                    # Restore soft deleted
                    for key, val in old_data.items():
                        if hasattr(obj, key):
                            column = inspect(Project).mapper.columns.get(key)
                            if isinstance(column.type, DateTime) and val:
                                setattr(obj, key, datetime.fromisoformat(val))
                            elif isinstance(column.type, Numeric) and val is not None:
                                setattr(obj, key, Decimal(str(val)))
                            else:
                                setattr(obj, key, val)
                else:
                    # Hard deleted: recreate
                    new_obj = model_cls()
                    for key, val in old_data.items():
                        if hasattr(new_obj, key):
                            column = inspect(model_cls).mapper.columns.get(key)
                            if isinstance(column.type, DateTime) and val:
                                setattr(new_obj, key, datetime.fromisoformat(val))
                            elif isinstance(column.type, Numeric) and val is not None:
                                setattr(new_obj, key, Decimal(str(val)))
                            else:
                                setattr(new_obj, key, val)
                    db.add(new_obj)
            else:
                # Other types: recreate
                new_obj = model_cls()
                for key, val in old_data.items():
                    if hasattr(new_obj, key):
                        column = inspect(model_cls).mapper.columns.get(key)
                        if isinstance(column.type, DateTime) and val:
                            setattr(new_obj, key, datetime.fromisoformat(val))
                        elif isinstance(column.type, Numeric) and val is not None:
                            setattr(new_obj, key, Decimal(str(val)))
                        else:
                            setattr(new_obj, key, val)
                db.add(new_obj)

        log.is_undone = 1
        db.commit()
        return JSONResponse({"success": True, "message": "Action undone successfully"})

    except Exception as e:
        db.rollback()
        print(f"Undo error: {e}")
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/projects/{project_id}/design-items/{item_id}/delete")
def project_delete_design_item(
    project_id: int,
    item_id: int,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    it = db.get(ProjectDesignItem, item_id)
    if not it or it.project_id != project_id:
        raise HTTPException(status_code=404)
    db.delete(it)
    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

@app.post("/projects/{project_id}/pricing")
def project_update_pricing(
    project_id: int,
    discount_percent: str = Form("100"),
    final_price_override: str = Form(""),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404)

    dp = _parse_decimal(discount_percent, Decimal("100")) or Decimal("100")
    if dp < 0:
        dp = Decimal("0")
    if dp > 100:
        dp = Decimal("100")

    fpo = _parse_decimal(final_price_override, None)
    project.discount_percent = dp
    project.final_price_override = fpo
    db.add(project)
    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

@app.get("/services", response_class=HTMLResponse)
def services_page(
    request: Request,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    q = request.query_params.get("q", "").strip()
    stmt = select(DesignService).order_by(DesignService.sort_order.asc(), DesignService.id.asc())
    if q:
        stmt = stmt.where(DesignService.name.contains(q))
    services = db.scalars(stmt).all()
    
    custom_attrs_a = SystemSetting.get_val(db, "custom_service_attrs_a", "正面,侧面,局部,鸟瞰")
    custom_attrs_b = SystemSetting.get_val(db, "custom_service_attrs_b", "日景,夜景,黄昏,阴天")
    
    return templates.TemplateResponse(
        "services.html",
        {
            "request": request,
            "user": user,
            "services": services,
            "q": q,
            "custom_attrs_a": custom_attrs_a,
            "custom_attrs_b": custom_attrs_b,
            "message": request.session.pop("message", None),
            "error": request.session.pop("error", None),
        },
    )

@app.post("/api/settings/service-attrs")
def update_service_attrs_settings(
    request: Request,
    custom_service_attrs_a: str = Form(""),
    custom_service_attrs_b: str = Form(""),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db)
):
    for key, val in [("custom_service_attrs_a", custom_service_attrs_a), ("custom_service_attrs_b", custom_service_attrs_b)]:
        setting = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
        if not setting:
            setting = SystemSetting(key=key, value=val.strip())
            db.add(setting)
        else:
            setting.value = val.strip()
    db.commit()
    request.session["message"] = "全局修饰词配置已更新"
    return RedirectResponse(url="/services", status_code=303)

@app.post("/services")
def services_create(
    name: str = Form(...),
    base_price: str = Form("0"),
    internal_price: str = Form("0"),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    n = name.strip()
    if not n:
        raise HTTPException(status_code=400, detail="名称不能为空")

    existing = db.scalar(select(DesignService).where(DesignService.name == n))
    if existing:
        raise HTTPException(status_code=400, detail="服务项已存在")

    bp = base_price.strip() or "0"
    ip = internal_price.strip() or "0"
    max_order = db.scalar(select(func.max(DesignService.sort_order)))
    next_order = int(max_order or 0) + 10
    svc = DesignService(
        name=n, 
        base_price=bp, 
        internal_price=ip, 
        is_active=1, 
        sort_order=next_order,
    )
    db.add(svc)
    db.commit()
    return RedirectResponse(url="/services", status_code=303)

@app.post("/services/{service_id}/update")
def services_update(
    service_id: int,
    name: str = Form(...),
    base_price: str = Form("0"),
    internal_price: str = Form("0"),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    svc = db.get(DesignService, service_id)
    if not svc:
        raise HTTPException(status_code=404)

    n = name.strip()
    if not n:
        raise HTTPException(status_code=400, detail="名称不能为空")

    existing = db.scalar(select(DesignService).where(DesignService.name == n))
    if existing and existing.id != svc.id:
        raise HTTPException(status_code=400, detail="服务名称已存在")

    bp = base_price.strip() or "0"
    ip = internal_price.strip() or "0"
    svc.name = n
    svc.base_price = bp
    svc.internal_price = ip
    db.add(svc)
    db.commit()
    return RedirectResponse(url="/services", status_code=303)

@app.post("/services/{service_id}/toggle")
def services_toggle(
    service_id: int,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    svc = db.get(DesignService, service_id)
    if not svc:
        raise HTTPException(status_code=404)
    svc.is_active = 0 if svc.is_active else 1
    db.add(svc)
    db.commit()
    return RedirectResponse(url="/services", status_code=303)

@app.post("/services/{service_id}/delete")
def services_delete(
    service_id: int,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    svc = db.get(DesignService, service_id)
    if not svc:
        raise HTTPException(status_code=404)

    used_count = db.scalar(
        select(func.count(ProjectDesignItem.id)).where(ProjectDesignItem.service_id == service_id)
    ) or 0

    # If already referenced by projects, do not hard-delete (keeps historical project records intact).
    if used_count > 0:
        svc.is_active = 0
        db.add(svc)
        db.commit()
        return RedirectResponse(url="/services", status_code=303)

    db.delete(svc)
    db.commit()
    normalize_design_service_order(db)
    return RedirectResponse(url="/services", status_code=303)

@app.post("/services/{service_id}/move")
def services_move(
    service_id: int,
    direction: str = Form(...),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    normalize_design_service_order(db)

    services = db.scalars(
        select(DesignService).order_by(DesignService.sort_order.asc(), DesignService.id.asc())
    ).all()
    idx = next((i for i, s in enumerate(services) if s.id == service_id), None)
    if idx is None:
        raise HTTPException(status_code=404)

    if direction == "up":
        if idx == 0:
            return RedirectResponse(url="/services", status_code=303)
        a = services[idx]
        b = services[idx - 1]
    elif direction == "down":
        if idx == len(services) - 1:
            return RedirectResponse(url="/services", status_code=303)
        a = services[idx]
        b = services[idx + 1]
    else:
        raise HTTPException(status_code=400, detail="direction 参数错误")

    a.sort_order, b.sort_order = b.sort_order, a.sort_order
    db.add(a)
    db.add(b)
    db.commit()
    return RedirectResponse(url="/services", status_code=303)

def get_effective_rate(db: Session, role: str, unit_type: str, source_item_id: Optional[int] = None) -> Decimal:
    """获取实际计算提成的有效单价：支持对内单价优先、本角色提成、降级设计师（staff）单价兜底。"""
    # 1. 优先使用关联设计内容的对内单价
    if source_item_id:
        item = db.get(ProjectDesignItem, source_item_id)
        if item and item.internal_unit_price and item.internal_unit_price > 0:
            return item.internal_unit_price

    # 2. 回退到基于角色的通用规则
    rule = db.scalar(
        select(CommissionRule).where(
            CommissionRule.role == role,
            CommissionRule.unit_type == unit_type
        )
    )
    if rule and rule.rate_per_unit > 0:
        return rule.rate_per_unit

    # 3. 降级套用设计师标准
    if role != "staff":
        staff_rule = db.scalar(
            select(CommissionRule).where(
                CommissionRule.role == "staff",
                CommissionRule.unit_type == unit_type
            )
        )
        if staff_rule:
            return staff_rule.rate_per_unit

    return Decimal("0.00")

def compute_commission_amount(db: Session, user: User, task: WorkItem) -> Decimal:
    """计算任务提成金额：使用统一的有效单价乘工作量。"""
    rate = get_effective_rate(db, user.role, task.unit_type, task.source_item_id)
    return _d2(task.workload_units * rate)


@app.get("/tasks", response_class=HTMLResponse)
def tasks_page(
    request: Request,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    projects = db.scalars(
        select(Project)
        .options(joinedload(Project.design_items).joinedload(ProjectDesignItem.service))
        .order_by(Project.id.desc())
    ).unique().all()
    people = db.scalars(select(User).order_by(User.id.asc())).all()

    # Stage concept removed
    stage_options = []

    q_kw = request.query_params.get("q", "").strip()
    q_project = request.query_params.get("project_id", "").strip()
    q_assignee = request.query_params.get("assignee_id", "").strip()

    try:
        q = select(WorkItem).order_by(WorkItem.id.desc())
        if user.role == "staff":
            q = q.where(WorkItem.assigned_to_user_id == user.id)

        if q_kw:
            q = q.where(WorkItem.title.contains(q_kw))
        if q_project.isdigit():
            q = q.where(WorkItem.project_id == int(q_project))
        if q_assignee.isdigit():
            q = q.where(WorkItem.assigned_to_user_id == int(q_assignee))

        tasks = db.scalars(q).all()

        rows = []
        for t in tasks:
            assignee = db.get(User, t.assigned_to_user_id)
            project = db.get(Project, t.project_id)
            amount = compute_commission_amount(db, assignee, t) if assignee else Decimal("0.00")
            rows.append({"task": t, "assignee": assignee, "project": project, "amount": amount})

        return templates.TemplateResponse(
            "tasks.html",
            {
                "request": request,
                "user": user,
                "projects": projects,
                "people": people,
                "rows": rows,
                "stage_options": stage_options,
                "message": request.session.pop("message", None),
                "error": request.session.pop("error", None),
                "q": q_kw,
                "project_id": q_project,
                "assignee_id": q_assignee,
            },
        )
    except Exception as e:
        import traceback
        return HTMLResponse(content=f"<h1>System Error</h1><pre>{traceback.format_exc()}</pre>", status_code=500)

@app.post("/tasks")
def tasks_create(
    project_id: int = Form(...),
    title: str = Form(None),
    # stage removed
    assigned_to_user_id: int = Form(...),
    workload_units: Decimal = Form(...),
    unit_type: str = Form("point"),
    design_item_id: Optional[int] = Form(None),
    return_to: str = Form(""),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    final_title = title
    source_id = None
    
    # If design_item_id is provided, look it up
    if design_item_id:
        item = db.get(ProjectDesignItem, design_item_id)
        if item:
            source_id = item.id
            if not final_title:
                final_title = item.service.name

    if not final_title:
        # Fallback if both title and item are missing (should be caught by UI, but safety first)
        final_title = "未命名任务"

    t = WorkItem(
        project_id=project_id,
        title=final_title,
        stage="", # Removed concept
        assigned_to_user_id=assigned_to_user_id,
        workload_units=workload_units,
        unit_type=unit_type,
        status="待办",
        source_item_id=source_id,
    )
    db.add(t)
    db.commit()

    if return_to == "project":
        return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

    return RedirectResponse(url="/tasks", status_code=303)

@app.get("/tasks/{task_id}", response_class=HTMLResponse)
# Route for Task Detail Page
def task_detail_page(
    request: Request,
    task_id: int,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    task = db.get(WorkItem, task_id)
    if not task:
        raise HTTPException(status_code=404)

    # Permission Check: Admin/Manager OR Assignee
    if user.role not in ["admin", "manager"] and task.assigned_to_user_id != user.id:
        raise HTTPException(status_code=403, detail="无权查看此任务")

    return templates.TemplateResponse(
        "task_detail.html",
        {
            "request": request,
            "user": user,
            "task": task,
            "UNIT_LABELS": {"point": "点数", "sheet": "张数"},
            "ROLE_LABELS": {"admin": "管理员", "manager": "项目经理", "staff": "员工", "finance": "财务"},
            "can_edit": user.role in ["admin", "manager"],
            "message": request.session.pop("message", None),
            "error": request.session.pop("error", None),
        },
    )

@app.get("/tasks/{task_id}/edit", response_class=HTMLResponse)
def task_edit_page(
    request: Request,
    task_id: int,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    task = db.get(WorkItem, task_id)
    if not task:
        raise HTTPException(status_code=404)

    projects = db.scalars(select(Project).order_by(Project.id.desc())).all()
    people = db.scalars(select(User).order_by(User.id.asc())).all()

    stage_options_raw: List[str] = []
    try:
        stage_options_raw = db.scalars(
            select(WorkItem.stage)
            .where(WorkItem.stage.is_not(None), WorkItem.stage != "")
            .distinct()
            .order_by(WorkItem.stage.asc())
        ).all()
    except Exception:
        stage_options_raw = []

    stage_options = sorted({(s or "").strip() for s in stage_options_raw if (s or "").strip()})

    return templates.TemplateResponse(
        "task_edit.html",
        {
            "request": request,
            "user": user,
            "task": task,
            "projects": projects,
            "people": people,
            "stage_options": stage_options,
            "message": request.session.pop("message", None),
            "error": request.session.pop("error", None),
            "title": f"编辑任务 - {task.title}",
        },
    )

@app.post("/tasks/{task_id}/update")
def task_update(
    request: Request,
    task_id: int,
    project_id: int = Form(...),
    title: str = Form(...),
    stage: str = Form("设计"),
    assigned_to_user_id: int = Form(...),
    workload_units: str = Form("0"),
    unit_type: str = Form("point"),
    is_completed: str = Form("0"),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    task = db.get(WorkItem, task_id)
    if not task:
        request.session["error"] = "任务不存在"
        return RedirectResponse(url="/tasks", status_code=303)

    wu = _parse_decimal(workload_units, Decimal("0.00")) or Decimal("0.00")
    if wu < 0:
        wu = Decimal("0.00")

    task.project_id = project_id
    task.title = title
    task.stage = stage
    task.assigned_to_user_id = assigned_to_user_id
    task.workload_units = wu
    task.unit_type = unit_type
    
    # Handle Status Update from Edit Form
    # If is_completed is checked, force status to done (compatibility)
    # Otherwise, rely on specific status field if we add it to the form later, 
    # but for now let's reuse is_completed for backward compat or if status is passed in form.
    # We will prioritize explicit status form field if it existed, but here we just handle is_completed.
    
    completed_flag = str(is_completed).strip() in {"1", "true", "True", "on"}
    if completed_flag:
        if task.status != "已完成":
            task.status = "已完成"
            if not task.completed_at:
                task.completed_at = datetime.utcnow()
    else:
        # If unchecking completed, revert to processing if it was done
        if task.status == "已完成":
            task.status = "进行中"
            task.completed_at = None
            
    db.add(task)
    db.commit()
    request.session["message"] = "任务已保存"
    return RedirectResponse(url=f"/tasks/{task_id}/edit", status_code=303)

@app.post("/tasks/{task_id}/delete")
def task_delete(
    request: Request,
    task_id: int,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    task = db.get(WorkItem, task_id)
    if not task:
        request.session["error"] = "任务不存在"
        return RedirectResponse(url="/tasks", status_code=303)

    db.delete(task)
    db.commit()
    request.session["message"] = "任务已删除"
    return RedirectResponse(url="/tasks", status_code=303)

@app.post("/tasks/{task_id}/complete")
def tasks_complete(
    task_id: int,
    return_to: str = Form(""),
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    t = db.get(WorkItem, task_id)
    if not t:
        raise HTTPException(status_code=404)

    if user.role == "staff" and t.assigned_to_user_id != user.id:
        raise HTTPException(status_code=403)

    if not t.completed_at:
        t.completed_at = datetime.utcnow()
        t.status = "已完成"
        db.add(t)
        db.commit()

    redirect_url = "/" if return_to == "dashboard" else "/tasks"
    return RedirectResponse(url=redirect_url, status_code=303)

@app.post("/tasks/{task_id}/status")
def task_update_status(
    task_id: int,
    status: str = Form(...),
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    t = db.get(WorkItem, task_id)
    if not t:
        raise HTTPException(status_code=404)

    # Permission check: Staff can only change to processing/review
    if user.role == "staff":
        if t.assigned_to_user_id != user.id:
            raise HTTPException(status_code=403, detail="Not your task")
        if status not in ["进行中", "审核中"]:
             # Allow staff to revert from review to processing? Yes.
             # Allow staff to complete? No, only Manager via approval (technically). 
             # But for flexibility, maybe allow staff to set to done if small team?
             # Implementation Plan says: Review -> Done is Manager's job.
             pass

    # State Transition Logic
    old_status = t.status
    t.status = status
    
    if status == "done" and not t.completed_at:
        t.completed_at = datetime.utcnow()
    elif status != "done" and t.completed_at:
        t.completed_at = None
        
    db.add(t)
    # Log it
    log_activity(db, t.project_id, user, "UPDATE_TASK", f"Task {t.title} status changed: {old_status} -> {status}")
    db.commit()
    
    # Return JSON if AJAX, or Redirect
    return JSONResponse({"ok": True, "status": t.status})

@app.post("/projects/{project_id}/distribute-tasks")
def project_distribute_tasks(
    project_id: int,
    design_item_id: int = Form(...),
    mode: str = Form(...), # 'full', 'pipeline'
    assignee_id: str = Form(""), # for full
    assignee_model: str = Form(""),
    assignee_render: str = Form(""),
    assignee_post: str = Form(""),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    def parse_optional_int(val: str) -> Optional[int]:
        s = val.strip()
        return int(s) if s.isdigit() else None

    uid_full = parse_optional_int(assignee_id)
    uid_model = parse_optional_int(assignee_model)
    uid_render = parse_optional_int(assignee_render)
    uid_post = parse_optional_int(assignee_post)
    
    item = db.get(ProjectDesignItem, design_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Design item not found")

    p = db.get(Project, project_id)
    if not p or item.project_id != project_id:
        raise HTTPException(status_code=404)

    # 统一的创建任务 helper 函数
    def create_task(stage: str, user_id: int, suffix: str = ""):
        if not user_id:
            return
        title = f"{item.full_name}"
        if suffix:
            title += f" ({suffix})"
            
        t = WorkItem(
            project_id=project_id,
            title=title,
            stage=stage,
            assigned_to_user_id=user_id,
            workload_units=item.quantity, # 默认取合同项的数量
            unit_type="point", # 默认提成计算单位是点数
            status="待办",
            source_item_id=item.id
        )
        db.add(t)

    if mode == "full":
        if not uid_full:
             raise HTTPException(status_code=400, detail="请选择负责人")
        create_task("全案", uid_full)
    
    elif mode == "pipeline":
        if uid_model: create_task("建模", uid_model, "建模")
        if uid_render: create_task("渲染", uid_render, "渲染")
        if uid_post: create_task("后期", uid_post, "后期")

    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

@app.get("/commissions", response_class=HTMLResponse)
def commissions_page(
    request: Request,
    user: User = Depends(require_roles("admin", "manager", "finance")),
    db: Session = Depends(get_db),
    month: Optional[str] = None,
):
    people = db.scalars(select(User).order_by(User.id.asc())).all()

    if month:
        try:
            y, m = month.split("-", 1)
            year = int(y)
            mon = int(m)
            start = datetime(year, mon, 1)
            if mon == 12:
                end = datetime(year + 1, 1, 1)
            else:
                end = datetime(year, mon + 1, 1)
        except Exception:
            month = None
            start = None
            end = None
    else:
        start = None
        end = None

    q = select(WorkItem).where(WorkItem.completed_at.is_not(None))
    if start and end:
        q = q.where(WorkItem.completed_at >= start, WorkItem.completed_at < end)
    q = q.order_by(WorkItem.completed_at.desc())
    tasks = db.scalars(q).all()

    totals: Dict[int, Decimal] = {}
    details = []
    
    # 获取项目经理管理提成比率
    pm_rate_str = SystemSetting.get_val(db, "manager_commission_rate", "0.10")
    pm_rate = Decimal(pm_rate_str)
    pm_percent = int(pm_rate * Decimal("100"))

    for t in tasks:
        assignee = db.get(User, t.assigned_to_user_id)
        project = db.get(Project, t.project_id)
        if not assignee:
            continue
        amount = compute_commission_amount(db, assignee, t)
        totals[assignee.id] = totals.get(assignee.id, Decimal("0.00")) + amount
        
        # 增加制作提成明细
        details.append({
            "task": t,
            "assignee": assignee,
            "project": project,
            "amount": amount,
            "desc": "制作提成"
        })
        
        # 增加项目经理管理提成
        if project and project.manager_id and project.manager_id != assignee.id:
            pm_user = db.get(User, project.manager_id)
            if pm_user:
                pm_amount = _d2(amount * pm_rate)
                if pm_amount > 0:
                    totals[pm_user.id] = totals.get(pm_user.id, Decimal("0.00")) + pm_amount
                    details.append({
                        "task": t,
                        "assignee": pm_user,
                        "project": project,
                        "amount": pm_amount,
                        "desc": f"管理提成 (基于{assignee.full_name or assignee.username}的{pm_percent}%提成)"
                    })

    totals_view = []
    for p in people:
        totals_view.append({"person": p, "total": totals.get(p.id, Decimal("0.00"))})

    rules = db.scalars(
        select(CommissionRule).order_by(CommissionRule.role.asc(), CommissionRule.unit_type.asc())
    ).all()

    return templates.TemplateResponse(
        "commissions.html",
        {
            "request": request,
            "user": user,
            "totals": totals_view,
            "details": details,
            "rules": rules,
            "month": month or "",
        },
    )

@app.post("/commission-rules")
async def commission_rules_update(
    role: str = Form(...),
    unit_type: str = Form("point"),
    rate_per_unit: Decimal = Form(...),
    user: User = Depends(require_roles("admin", "finance")),
    db: Session = Depends(get_db),
):
    rule = db.scalar(
        select(CommissionRule).where(
            CommissionRule.role == role,
            CommissionRule.unit_type == unit_type,
        )
    )
    if not rule:
        rule = CommissionRule(role=role, unit_type=unit_type, rate_per_unit=rate_per_unit)
        db.add(rule)
    else:
        rule.rate_per_unit = rate_per_unit
    db.commit()
    return RedirectResponse("/commissions", status_code=303)

@app.post("/commission-rules/{rule_id}/delete")
def commission_rule_delete(
    rule_id: int,
    user: User = Depends(require_roles("admin", "finance")),
    db: Session = Depends(get_db)
):
    rule = db.get(CommissionRule, rule_id)
    if rule:
        db.delete(rule)
        db.commit()
    return RedirectResponse("/commissions", status_code=303)

@app.get("/switch-view")
def switch_view(to: str, request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401)
    if to in ["staff", "manage"]:
        request.session["current_view"] = to
    if to == "staff":
        return RedirectResponse(url="/my-performance", status_code=303)
    else:
        return RedirectResponse(url="/projects", status_code=303)

# 客户管理相关路由
@app.get("/clients", response_class=HTMLResponse, name="clients_page")
async def clients_page(
    request: Request,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    q = request.query_params.get("q", "").strip()
    stmt = select(Client).options(joinedload(Client.contacts)).order_by(Client.name.asc())

    if q:
        # 客户名 or 联系人信息命中
        stmt = stmt.join(Contact, Client.id == Contact.client_id, isouter=True).where(
            (Client.name.contains(q))
            | (Contact.name.contains(q))
            | (Contact.mobile.contains(q))
            | (Contact.phone.contains(q))
            | (Contact.email.contains(q))
        )
        stmt = stmt.distinct()

    clients = db.execute(stmt).unique().scalars().all()
    
    # 确保每个客户都有 contacts 属性
    for client in clients:
        if not hasattr(client, 'contacts'):
            client.contacts = []
    
    return templates.TemplateResponse(
        "clients.html",
        {
            "request": request,
            "user": user,
            "clients": clients,
            "q": q,
            "message": request.session.pop("message", None),
            "error": request.session.pop("error", None),
            "show_add_modal": request.session.pop("show_add_modal", None),
        },
    )

@app.post("/clients")
async def create_client(
    request: Request,
    name: str = Form(...),
    contact_person: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    # 检查客户名称是否已存在
    existing = db.scalar(select(Client).where(Client.name == name))
    if existing:
        request.session["error"] = f"客户 '{name}' 已存在"
        request.session["show_add_modal"] = "true"
        return RedirectResponse("/clients", status_code=303)

    # 创建新客户（联系人信息不再写入 clients 表）
    client = Client(
        name=name,
        address=address,
        notes=notes,
    )
    db.add(client)
    db.flush()

    # 兼容旧 UI：如果填写了联系人/电话/邮箱，则自动创建主要联系人
    if (contact_person or "").strip() or (phone or "").strip() or (email or "").strip():
        primary = Contact(
            client_id=client.id,
            name=(contact_person or "").strip() or "未命名联系人",
            mobile=(phone or "").strip(),
            email=(email or "").strip(),
            is_primary=True,
        )
        db.add(primary)

    db.commit()
    request.session["message"] = f"成功添加客户: {name}"
    return RedirectResponse("/clients", status_code=303)

@app.post("/clients/{client_id}/update")
async def update_client(
    request: Request,
    client_id: int,
    name: str = Form(...),
    contact_person: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    client = (
        db.query(Client)
        .options(joinedload(Client.contacts), joinedload(Client.projects))
        .filter(Client.id == client_id)
        .first()
    )
    if not client:
        request.session["error"] = "客户不存在"
        return RedirectResponse("/clients", status_code=303)

    primary_contact = None
    for c in (client.contacts or []):
        if c.is_primary:
            primary_contact = c
            break

    # 检查客户名称是否与其他客户重复
    existing = db.scalar(select(Client).where(Client.name == name, Client.id != client_id))
    if existing:
        request.session["error"] = f"客户名称 '{name}' 已被使用"
        return RedirectResponse(f"/clients/{client_id}/edit", status_code=303)

    # 更新客户信息
    client.name = name
    client.address = address
    client.notes = notes

    # 兼容旧 UI：更新/创建主要联系人
    cp = (contact_person or "").strip()
    ph = (phone or "").strip()
    em = (email or "").strip()
    if cp or ph or em:
        primary = db.scalar(
            select(Contact).where(Contact.client_id == client_id, Contact.is_primary == True)
        )
        if not primary:
            primary = Contact(client_id=client_id, name=cp or "未命名联系人", is_primary=True)
            db.add(primary)
        else:
            if cp:
                primary.name = cp
        if ph:
            primary.mobile = ph
        if em:
            primary.email = em
        primary.updated_at = datetime.utcnow()

    db.commit()

    request.session["message"] = f"成功更新客户: {name}"
    return RedirectResponse("/clients", status_code=303)

@app.post("/clients/{client_id}/delete")
async def delete_client(
    request: Request,
    client_id: int,
    user: User = Depends(require_roles("admin")),  # 只有管理员可以删除客户
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if not client:
        request.session["error"] = "客户不存在"
        return RedirectResponse("/clients", status_code=303)

    # 检查客户是否有关联的项目
    has_projects = db.scalar(select(Project).where(Project.client_id == client_id).limit(1))
    if has_projects:
        request.session["error"] = f"无法删除客户 '{client.name}'，该客户有关联的项目"
        return RedirectResponse("/clients", status_code=303)

    db.delete(client)
    db.commit()
    request.session["message"] = f"已删除客户: {client.name}"
    return RedirectResponse("/clients", status_code=303)

@app.get("/clients/{client_id}/edit", response_class=HTMLResponse)
async def edit_client_page(
    request: Request,
    client_id: int,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    client = (
        db.query(Client)
        .options(joinedload(Client.contacts), joinedload(Client.projects))
        .filter(Client.id == client_id)
        .first()
    )
    if not client:
        request.session["error"] = "客户不存在"
        return RedirectResponse("/clients", status_code=303)

    primary_contact = None
    for c in (client.contacts or []):
        if c.is_primary:
            primary_contact = c
            break

    return templates.TemplateResponse(
        "edit_client.html",
        {
            "request": request,
            "user": user,
            "client": client,
            "primary_contact": primary_contact,
            "title": f"编辑客户 - {client.name}",
            "message": request.session.pop("message", None),
            "error": request.session.pop("error", None),
        },
    )

@app.get("/backup", response_class=HTMLResponse)
async def backup_page(
    request: Request,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db)
):
    print("\n=== 开始处理备份页面请求 ===")
    try:
        # 获取备份配置
        print("1. 获取备份配置...")
        try:
            config = ensure_backup_config(db)
            print(f"   配置加载成功: {config}")
        except Exception as e:
            error_msg = f"获取备份配置失败: {str(e)}"
            print(f"   {error_msg}")
            return templates.TemplateResponse(
                "backup.html",
                {
                    "request": request,
                    "user": user,
                    "backup_files": [],
                    "config": None,
                    "error": error_msg
                }
            )
        
        backup_dir = config.backup_path
        print(f"2. 备份目录: {backup_dir}")
        
        # 确保备份目录存在
        print("3. 检查/创建备份目录...")
        try:
            if not os.path.exists(backup_dir):
                print(f"   目录不存在，创建目录: {backup_dir}")
                os.makedirs(backup_dir, exist_ok=True)
                print("   目录创建成功")
            else:
                print("   目录已存在")
        except Exception as e:
            error_msg = f"创建备份目录失败: {str(e)}"
            print(f"   {error_msg}")
            return templates.TemplateResponse(
                "backup.html",
                {
                    "request": request,
                    "user": user,
                    "backup_files": [],
                    "config": config,
                    "error": error_msg
                }
            )
        
        # 获取备份文件列表
        print("4. 扫描备份文件...")
        backups = []
        try:
            for filename in os.listdir(backup_dir):
                if not filename.endswith(".db"):
                    continue
                    
                filepath = os.path.join(backup_dir, filename)
                try:
                    stat = os.stat(filepath)
                    backups.append({
                        "name": filename,
                        "size": stat.st_size,
                        "size_mb": round(stat.st_size / (1024 * 1024), 2),  # 转换为MB并保留两位小数
                        "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        "path": filepath
                    })
                    print(f"   找到备份文件: {filename} ({backups[-1]['size_mb']:.2f} MB)")
                except Exception as e:
                    print(f"   处理备份文件 {filename} 时出错: {str(e)}")
            
            print(f"   共找到 {len(backups)} 个备份文件")
            
            # 按修改时间倒序排序
            backups.sort(key=lambda x: x["mtime"], reverse=True)
            
            # 清理旧备份
            if len(backups) > config.max_backups:
                print(f"5. 清理旧备份 (保留最新的 {config.max_backups} 个)...")
                old_backups = sorted(backups, key=lambda x: x["mtime"])[:len(backups) - config.max_backups]
                for backup in old_backups:
                    try:
                        os.remove(backup["path"])
                        backups.remove(backup)
                        print(f"   已删除旧备份: {backup['name']}")
                    except Exception as e:
                        print(f"   删除旧备份 {backup['name']} 失败: {str(e)}")
            
            # 准备模板上下文
            context = {
                "request": request,
                "user": user,
                "backup_files": backups,
                "config": config,
                "message": request.session.pop("message", None),
                "error": request.session.pop("error", None)
            }
            
            print("6. 渲染模板...")
            return templates.TemplateResponse("backup.html", context)
            
        except Exception as e:
            error_msg = f"处理备份文件时出错: {str(e)}"
            print(f"   {error_msg}")
            import traceback
            traceback.print_exc()
            
            # 返回错误信息
            return templates.TemplateResponse(
                "backup.html",
                {
                    "request": request,
                    "user": user,
                    "backup_files": [],
                    "config": config,
                    "error": error_msg
                },
                status_code=500
            )
            
    except Exception as e:
        error_msg = f"处理请求时发生未捕获的异常: {str(e)}"
        print(f"\n!!! 严重错误: {error_msg}")
        import traceback
        traceback.print_exc()
        
        # 返回一个简单的错误页面
        return HTMLResponse(
            content=f"""
            <html>
                <head><title>服务器错误</title></head>
                <body>
                    <h1>500 - 服务器内部错误</h1>
                    <p>处理请求时发生错误，请查看服务器日志获取详细信息。</p>
                    <pre>{error_msg}</pre>
                    <p><a href="/">返回首页</a></p>
                </body>
            </html>
            """,
            status_code=500
        )
    finally:
        print("=== 备份页面请求处理完成 ===\n")

@app.post("/backup/create")
async def create_backup(
    request: Request,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db)
):
    """创建数据库备份"""
    if not DATABASE_URL.startswith("sqlite:///"):
        request.session["error"] = "当前仅支持SQLite数据库的备份"
        return RedirectResponse("/backup", status_code=303)
    
    try:
        # 获取备份配置
        config = ensure_backup_config(db)
        
        # 确保备份目录存在
        os.makedirs(config.backup_path, exist_ok=True)
        
        # 生成带时间戳的备份文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(config.backup_path, f"{config.backup_prefix}{timestamp}.db")
        
        def _sqlite_backup_with_retry(src_path: str, dest_path: str) -> None:
            last_err: Optional[Exception] = None
            for i in range(8):
                try:
                    src_conn = sqlite3.connect(src_path)
                    try:
                        dest_conn = sqlite3.connect(dest_path)
                        try:
                            src_conn.backup(dest_conn)
                            return
                        finally:
                            dest_conn.close()
                    finally:
                        src_conn.close()
                except Exception as e:
                    last_err = e
                    time.sleep(0.2 * (i + 1))
            raise RuntimeError(str(last_err) if last_err else "backup failed")

        src = _sqlite_db_file_path() or os.path.abspath("./data/app.db")
        if not os.path.exists(src):
            raise FileNotFoundError(f"数据库文件不存在: {src}")
        try:
            engine.dispose()
        except Exception:
            pass
        _sqlite_backup_with_retry(src, backup_file)
        
        request.session["message"] = f"备份创建成功: {os.path.basename(backup_file)}"
    except Exception as e:
        request.session["error"] = f"备份失败: {str(e)}"
    
    return RedirectResponse("/backup", status_code=303)


def _pick_directory(initial_dir: str) -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    path = filedialog.askdirectory(initialdir=initial_dir or os.path.expanduser("~"), title="选择备份目录")
    try:
        root.destroy()
    except Exception:
        pass
    return str(path or "")


@app.get("/backup/select-path")
async def select_backup_path(
    request: Request,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    config = ensure_backup_config(db)
    try:
        path = await asyncio.to_thread(_pick_directory, config.backup_path)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e), "path": ""}, status_code=500)

    if not path:
        return {"ok": True, "cancelled": True, "path": ""}
    return {"ok": True, "cancelled": False, "path": path}

@app.get("/backup/download/{filename}")
async def download_backup(
    filename: str,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db)
):
    """下载备份文件"""
    # 防止目录遍历攻击
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="无效的文件名")
    
    # 获取备份配置
    config = ensure_backup_config(db)
    backup_path = os.path.join(config.backup_path, filename)
    
    if not os.path.exists(backup_path):
        raise HTTPException(status_code=404, detail="备份文件不存在")
    
    return FileResponse(backup_path, filename=filename, media_type="application/octet-stream")

@app.post("/backup/restore")
async def restore_backup(
    request: Request,
    backup_file: str = Form(...),
    user: User = Depends(require_roles("admin")),  # 只有管理员可以恢复备份
    db: Session = Depends(get_db)
):
    """从备份恢复数据库"""
    if not DATABASE_URL.startswith("sqlite:///"):
        request.session["error"] = "当前仅支持SQLite数据库的恢复"
        return RedirectResponse("/backup", status_code=303)
    
    # 防止目录遍历攻击
    if ".." in backup_file or "/" in backup_file or "\\" in backup_file:
        request.session["error"] = "无效的备份文件"
        return RedirectResponse("/backup", status_code=303)
    
    # 获取备份配置
    config = ensure_backup_config(db)
    backup_path = os.path.join(config.backup_path, backup_file)
    
    if not os.path.exists(backup_path):
        request.session["error"] = "备份文件不存在"
        return RedirectResponse("/backup", status_code=303)
    
    try:
        global engine
        global SessionLocal
        # 停止当前数据库连接
        engine.dispose()
        
        # 备份当前数据库
        current_db = _sqlite_db_file_path() or os.path.abspath("./data/app.db")
        backup_current = os.path.join(config.backup_path, f"before_restore_{int(datetime.now().timestamp())}.db")
        if os.path.exists(current_db):
            shutil.copy2(current_db, backup_current)
        
        # 恢复备份
        shutil.copy2(backup_path, current_db)
        
        # 重新连接数据库
        engine = create_engine(
            DATABASE_URL,
            connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        
        # 更新会话中的用户信息
        request.session["message"] = f"数据库已从备份恢复: {backup_file}"
    except Exception as e:
        request.session["error"] = f"恢复失败: {str(e)}"
    
    return RedirectResponse("/backup", status_code=303)


@app.delete("/backup/delete/{filename}")
async def delete_backup(
    filename: str,
    user: User = Depends(require_roles("admin")),  # 只有管理员可以删除备份
    db: Session = Depends(get_db)
):
    """删除备份文件"""
    # 防止目录遍历攻击
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="无效的文件名")
    
    # 获取备份配置
    config = ensure_backup_config(db)
    backup_path = os.path.join(config.backup_path, filename)
    
    if not os.path.exists(backup_path):
        raise HTTPException(status_code=404, detail="备份文件不存在")
    
    try:
        os.remove(backup_path)
        return {"status": "success", "message": f"备份 {filename} 已删除"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@app.post("/backup/config/update")
async def update_backup_config(
    request: Request,
    backup_path: str = Form(...),
    backup_prefix: str = Form(...),
    max_backups: int = Form(...),
    user: User = Depends(require_roles("admin", "manager")),  # 管理员和项目经理可以更新配置
    db: Session = Depends(get_db)
):
    """更新备份配置"""
    # 验证输入
    if not backup_path or not backup_prefix:
        request.session["error"] = "备份路径和前缀不能为空"
        return RedirectResponse("/backup", status_code=303)
    
    if max_backups < 1:
        request.session["error"] = "最大备份数量必须大于0"
        return RedirectResponse("/backup", status_code=303)
    
    # 获取或创建配置
    config = ensure_backup_config(db)
    
    # 更新配置
    old_path = config.backup_path
    config.backup_path = backup_path
    config.backup_prefix = backup_prefix
    config.max_backups = max_backups
    config.updated_by = user.id
    
    # 如果路径改变，确保新目录存在
    if old_path != backup_path:
        try:
            os.makedirs(backup_path, exist_ok=True)
        except Exception as e:
            request.session["error"] = f"创建备份目录失败: {str(e)}"
            return RedirectResponse("/backup", status_code=303)
    
    db.commit()
    
    request.session["message"] = "备份配置已更新"
    return RedirectResponse("/backup", status_code=303)


# 客户联系人管理路由
@app.get("/clients/{client_id}/contacts", response_class=HTMLResponse, name="client_contacts_page")
async def client_contacts_page(
    request: Request,
    client_id: int,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    """显示客户联系人管理页面"""
    client = (
        db.query(Client)
        .options(joinedload(Client.contacts))
        .filter(Client.id == client_id)
        .first()
    )

    if not client:
        raise HTTPException(status_code=404, detail="客户不存在")
    
    return templates.TemplateResponse(
        "client_contacts.html",
        {
            "request": request,
            "user": user,
            "client": client,
            "message": request.session.pop("message", None),
            "error": request.session.pop("error", None),
        },
    )


@app.get("/api/contacts/{contact_id}")
async def get_contact_api(
    contact_id: int,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="联系人不存在")
    return {
        "id": contact.id,
        "client_id": contact.client_id,
        "name": contact.name,
        "position": contact.position,
        "department": contact.department,
        "phone": contact.phone,
        "mobile": contact.mobile,
        "email": contact.email,
        "wechat": contact.wechat,
        "is_primary": bool(contact.is_primary),
        "notes": contact.notes,
    }


@app.post("/clients/{client_id}/contacts/add", name="add_contact")
async def add_contact(
    request: Request,
    client_id: int,
    name: str = Form(...),
    position: str = Form(""),
    department: str = Form(""),
    phone: str = Form(""),
    mobile: str = Form(""),
    email: str = Form(""),
    wechat: str = Form(""),
    notes: str = Form(""),
    is_primary: bool = Form(False),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    """添加新联系人"""
    # 检查客户是否存在
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="客户不存在")
    
    try:
        # 如果设置为主要联系人，则先取消其他联系人的主要状态
        if is_primary:
            db.query(Contact).filter(
                Contact.client_id == client_id,
                Contact.is_primary == True
            ).update({"is_primary": False})
        
        # 创建新联系人
        contact = Contact(
            client_id=client_id,
            name=name,
            position=position,
            department=department,
            phone=phone,
            mobile=mobile,
            email=email,
            wechat=wechat,
            notes=notes,
            is_primary=is_primary,
        )
        db.add(contact)
        db.commit()
        
        request.session["message"] = f"成功添加联系人 {name}"
    except Exception as e:
        db.rollback()
        request.session["error"] = f"添加联系人失败: {str(e)}"
    
    return RedirectResponse(f"/clients/{client_id}/contacts", status_code=303)


@app.post("/clients/{client_id}/contacts/{contact_id}/edit", name="edit_contact")
async def edit_contact(
    request: Request,
    client_id: int,
    contact_id: int,
    name: str = Form(...),
    position: str = Form(""),
    department: str = Form(""),
    phone: str = Form(""),
    mobile: str = Form(""),
    email: str = Form(""),
    wechat: str = Form(""),
    notes: str = Form(""),
    is_primary: bool = Form(False),
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    """编辑联系人"""
    # 检查联系人和客户是否存在
    contact = db.query(Contact).filter(
        Contact.id == contact_id,
        Contact.client_id == client_id
    ).first()
    
    if not contact:
        raise HTTPException(status_code=404, detail="联系人不存在")
    
    try:
        # 如果设置为主要联系人，则先取消其他联系人的主要状态
        if is_primary and not contact.is_primary:
            db.query(Contact).filter(
                Contact.client_id == client_id,
                Contact.is_primary == True,
                Contact.id != contact_id
            ).update({"is_primary": False})
        
        # 更新联系人信息
        contact.name = name
        contact.position = position
        contact.department = department
        contact.phone = phone
        contact.mobile = mobile
        contact.email = email
        contact.wechat = wechat
        contact.notes = notes
        contact.is_primary = is_primary
        contact.updated_at = datetime.utcnow()
        
        db.commit()
        request.session["message"] = f"成功更新联系人 {name}"
    except Exception as e:
        db.rollback()
        request.session["error"] = f"更新联系人失败: {str(e)}"
    
    return RedirectResponse(f"/clients/{client_id}/contacts", status_code=303)


@app.post("/clients/{client_id}/contacts/{contact_id}/delete", name="delete_contact")
async def delete_contact(
    request: Request,
    client_id: int,
    contact_id: int,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    """删除联系人"""
    # 检查联系人和客户是否存在
    contact = db.query(Contact).filter(
        Contact.id == contact_id,
        Contact.client_id == client_id
    ).first()
    
    if not contact:
        raise HTTPException(status_code=404, detail="联系人不存在")
    
    try:
        contact_name = contact.name
        db.delete(contact)
        db.commit()
        request.session["message"] = f"成功删除联系人 {contact_name}"
    except Exception as e:
        db.rollback()
        request.session["error"] = f"删除联系人失败: {str(e)}"
    
    return RedirectResponse(f"/clients/{client_id}/contacts", status_code=303)


@app.post("/clients/{client_id}/contacts/{contact_id}/set_primary", name="set_primary_contact")
async def set_primary_contact(
    request: Request,
    client_id: int,
    contact_id: int,
    user: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
):
    """设置主要联系人"""
    # 检查联系人和客户是否存在
    contact = db.query(Contact).filter(
        Contact.id == contact_id,
        Contact.client_id == client_id
    ).first()
    
    if not contact:
        raise HTTPException(status_code=404, detail="联系人不存在")
    
    try:
        # 取消当前主要联系人的主要状态
        db.query(Contact).filter(
            Contact.client_id == client_id,
            Contact.is_primary == True,
            Contact.id != contact_id
        ).update({"is_primary": False})
        
        # 设置新的主要联系人
        contact.is_primary = True
        contact.updated_at = datetime.utcnow()
        
        db.commit()
        return {"status": "success", "message": f"已将 {contact.name} 设置为主要联系人"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"设置主要联系人失败: {str(e)}")
@app.get("/settings/finance", response_class=HTMLResponse)
def settings_finance_page(request: Request, db: Session = Depends(get_db)):
    user = _get_user_from_session(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if user.role not in ["admin", "finance", "manager"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    settings_list = db.scalars(select(SystemSetting)).all()
    settings_map = {s.key: s.value for s in settings_list}
    
    # Defaults if missing
    defaults = {
        "commission_rate_min": "0.15",
        "commission_rate_warning": "0.20",
        "commission_rate_max": "0.25",
        "manager_commission_rate": "0.10",
        "price_modeling": "80.00",
        "price_rendering": "50.00",
        "price_post": "50.00",
        "default_ratio_plan": '{"方案": 5, "建模": 12, "渲染": 5, "后期": 8}',
        "local_path_root": "\\\\Server\\p",
        "all_skills": "建模,渲染,后期",
    }
    for k, v in defaults.items():
        if k not in settings_map:
            settings_map[k] = v

    import json
    ratio_plan_str = settings_map.get("default_ratio_plan", "")
    try:
        ratio_plan = json.loads(ratio_plan_str)
    except Exception:
        ratio_plan = {"方案": 5, "建模": 12, "渲染": 5, "后期": 8}

    settings_map["ratio_scheme_1"] = ratio_plan.get("方案", 5)
    settings_map["ratio_scheme_2"] = ratio_plan.get("建模", 12)
    settings_map["ratio_scheme_3"] = ratio_plan.get("渲染", 5)
    settings_map["ratio_scheme_4"] = ratio_plan.get("后期", 8)

    return templates.TemplateResponse(
        "settings_finance.html",
        {
            "request": request,
            "user": user,
            "settings": settings_map,
            "current_year": datetime.now().year,
        },
    )

@app.post("/api/settings/finance")
async def update_finance_settings(
    request: Request,
    db: Session = Depends(get_db)
):
    user = _get_user_from_session(request, db)
    if not user or user.role not in ["admin", "finance", "manager"]:
         raise HTTPException(status_code=403, detail="Permission denied")

    form = await request.form()
    
    # 获取并拼接默认比例方案的 JSON
    ratio_1 = form.get("ratio_scheme_1", "0").strip()
    ratio_2 = form.get("ratio_scheme_2", "0").strip()
    ratio_3 = form.get("ratio_scheme_3", "0").strip()
    ratio_4 = form.get("ratio_scheme_4", "0").strip()
    
    try:
        r1 = int(float(ratio_1 or 0))
        r2 = int(float(ratio_2 or 0))
        r3 = int(float(ratio_3 or 0))
        r4 = int(float(ratio_4 or 0))
    except ValueError:
        r1, r2, r3, r4 = 0, 0, 0, 0

    import json
    default_ratio_plan_val = json.dumps({
        "方案": r1,
        "建模": r2,
        "渲染": r3,
        "后期": r4
    }, ensure_ascii=False)

    keys = [
        "price_modeling", "price_rendering", "price_post", "local_path_root", "all_skills"
    ]
    percentage_keys = [
        "commission_rate_min", "commission_rate_warning", "commission_rate_max",
        "manager_commission_rate"
    ]
    
    # 处理常规设置
    for key in keys:
        val = form.get(key)
        if val is not None:
            setting = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
            if not setting:
                setting = SystemSetting(key=key, value=str(val))
                db.add(setting)
            else:
                setting.value = str(val)

    # 处理百分比设置 (例如：前端输入 10，保存为 0.10)
    for key in percentage_keys:
        val = form.get(key)
        if val is not None:
            try:
                val_dec = str(Decimal(val) / Decimal("100.00"))
            except Exception:
                val_dec = "0.00"
            setting = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
            if not setting:
                setting = SystemSetting(key=key, value=val_dec)
                db.add(setting)
            else:
                setting.value = val_dec
                
    # 单独处理默认比例方案 JSON 保存
    plan_setting = db.scalar(select(SystemSetting).where(SystemSetting.key == "default_ratio_plan"))
    if not plan_setting:
        plan_setting = SystemSetting(key="default_ratio_plan", value=default_ratio_plan_val)
        db.add(plan_setting)
    else:
        plan_setting.value = default_ratio_plan_val
    
    db.commit()
    request.session["message"] = "财务参数已更新"
    return RedirectResponse(url="/settings/finance", status_code=303)

if __name__ == "__main__":
    import uvicorn
    # 明确指定 host 为 0.0.0.0 以便局域网访问
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)
