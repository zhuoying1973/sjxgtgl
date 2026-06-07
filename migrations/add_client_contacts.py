"""Add client contacts

Revision ID: 1a2b3c4d5e6f
Revises: 
Create Date: 2025-12-17 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e6f'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # 1. 重命名现有表
    op.rename_table('clients', 'clients_old')
    
    # 2. 创建新的clients表
    op.create_table(
        'clients',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(200), nullable=False, index=True),
        sa.Column('client_type', sa.String(50), nullable=False, server_default='company'),
        sa.Column('tax_id', sa.String(100), nullable=True),
        sa.Column('address', sa.String(500), nullable=True, default=''),
        sa.Column('notes', sa.Text(), nullable=True, default=''),
        sa.Column('status', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP'), onupdate=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id')
    )
    
    # 3. 创建联系人表
    op.create_table(
        'contact_persons',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('position', sa.String(100), nullable=True, default=''),
        sa.Column('department', sa.String(100), nullable=True, default=''),
        sa.Column('phone', sa.String(50), nullable=True, default=''),
        sa.Column('mobile', sa.String(50), nullable=True, default=''),
        sa.Column('email', sa.String(100), nullable=True, default=''),
        sa.Column('wechat', sa.String(100), nullable=True, default=''),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('notes', sa.Text(), nullable=True, default=''),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP'), onupdate=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # 4. 迁移旧数据
    op.execute('''
        INSERT INTO clients (id, name, address, notes, created_at, updated_at)
        SELECT id, name, address, notes, created_at, updated_at 
        FROM clients_old
    ''')
    
    # 5. 将旧的联系人信息迁移到新表
    op.execute('''
        INSERT INTO contact_persons 
        (client_id, name, phone, email, is_primary, created_at, updated_at)
        SELECT id, contact_person, phone, email, 1, created_at, updated_at 
        FROM clients_old 
        WHERE COALESCE(contact_person, '') != ''
    ''')
    
    # 6. 更新项目表的外键关系
    op.alter_column('projects', 'client_id', 
                   existing_type=sa.INTEGER(),
                   nullable=True)
    
    # 7. 添加主要联系人字段到项目表
    op.add_column('projects', 
        sa.Column('primary_contact_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_projects_primary_contact', 'projects', 'contact_persons',
        ['primary_contact_id'], ['id']
    )
    
    # 8. 删除旧表
    op.drop_table('clients_old')

def downgrade():
    # 回滚操作（简化版，实际使用时需要更详细的回滚逻辑）
    op.drop_table('contact_persons')
    op.drop_table('clients')
