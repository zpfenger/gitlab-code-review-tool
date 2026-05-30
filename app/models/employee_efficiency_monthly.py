"""人员能效月度汇总表（人 x 月聚合）"""
from sqlalchemy import Column, String, Integer, Text, Index, UniqueConstraint
from app.models.base import BaseModel


class EmployeeEfficiencyMonthly(BaseModel):
    """人员能效月度汇总表

    每行记录某人某月的代码量汇总、LLM 月度评分、月度工作总结。
    由 EfficiencyMonthlyAggregator 在每月1日定时任务中生成。
    """
    __tablename__ = "employee_efficiency_monthly"

    # 人员维度
    author_email = Column(String(200), nullable=False, comment="提交者邮箱")
    author_name = Column(String(100), nullable=False, comment="提交者显示名")
    year_month = Column(String(7), nullable=False, comment="统计月份，格式 YYYY-MM")

    # 代码量统计（从 daily 求和）
    commits_count = Column(Integer, nullable=False, default=0, comment="提交次数")
    additions = Column(Integer, nullable=False, default=0, comment="新增行数")
    deletions = Column(Integer, nullable=False, default=0, comment="删除行数")
    files_changed = Column(Integer, nullable=False, default=0, comment="涉及文件数")
    new_files = Column(Integer, nullable=False, default=0, comment="新建文件数")
    deleted_files = Column(Integer, nullable=False, default=0, comment="删除文件数")
    active_days = Column(Integer, nullable=False, default=0, comment="本月活跃天数")

    # 涉及项目（JSON 数组，合并去重）
    projects_involved = Column(Text, nullable=False, default="[]",
                                comment='涉及项目名 JSON 数组')

    # LLM 月度产出
    review_score = Column(Integer, nullable=True, comment="月度平均评分 0-100")
    review_grade = Column(String(10), nullable=True,
                           comment="等级：优秀/良好/一般/待改进")
    review_summary = Column(Text, nullable=True, comment="LLM 月度评分简述")
    work_summary = Column(Text, nullable=True,
                           comment="LLM 月度工作总结 JSON 数组")
    summary_top_n = Column(Integer, nullable=True, default=10,
                            comment="生成时使用的 top_n")

    # 状态
    llm_status = Column(String(20), nullable=False, default="pending",
                         comment="pending/success/failed/skipped")
    llm_error = Column(Text, nullable=True, comment="LLM 失败原因")

    __table_args__ = (
        UniqueConstraint("author_email", "year_month",
                          name="uq_employee_efficiency_monthly_email_month"),
        Index("idx_employee_efficiency_monthly_year_month", "year_month"),
        Index("idx_employee_efficiency_monthly_email_month",
              "author_email", "year_month"),
    )

    def __repr__(self):
        return (f"<EmployeeEfficiencyMonthly(email='{self.author_email}', "
                f"month={self.year_month}, score={self.review_score})>")
