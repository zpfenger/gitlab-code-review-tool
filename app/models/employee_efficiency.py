"""人员能效明细表（人 × 天聚合）"""
from sqlalchemy import Column, String, Integer, Date, Text, Index, UniqueConstraint
from app.models.base import BaseModel


class EmployeeEfficiencyDaily(BaseModel):
    """人员能效明细表

    每行记录某人某一天的代码量、LLM 评分、工作总结（跨项目跨分支已合并去重）。
    由 EfficiencyAggregator 在日报任务跑完时写入，唯一索引保证幂等。
    """
    __tablename__ = "employee_efficiency_daily"

    # 人员维度
    author_email = Column(String(200), nullable=False, comment="提交者邮箱（主维度）")
    author_name = Column(String(100), nullable=False, comment="提交者显示名")
    stat_date = Column(Date, nullable=False, comment="统计日期（自然日）")

    # 代码量统计
    commits_count = Column(Integer, nullable=False, default=0, comment="提交次数（去重后）")
    additions = Column(Integer, nullable=False, default=0, comment="新增行数")
    deletions = Column(Integer, nullable=False, default=0, comment="删除行数")
    files_changed = Column(Integer, nullable=False, default=0, comment="涉及文件数")
    new_files = Column(Integer, nullable=False, default=0, comment="新建文件数")
    deleted_files = Column(Integer, nullable=False, default=0, comment="删除文件数")

    # 涉及项目（JSON 数组字符串）
    projects_involved = Column(Text, nullable=False, default="[]",
                                comment='涉及项目名 JSON 数组 ["proj-a","proj-b"]')

    # LLM 产出
    review_score = Column(Integer, nullable=True, comment="综合评分 0-100")
    review_grade = Column(String(10), nullable=True,
                           comment="等级：优秀/良好/一般/待改进")
    review_summary = Column(Text, nullable=True, comment="评分简述（1-2 句）")
    work_summary = Column(Text, nullable=True,
                           comment="LLM 工作总结 JSON 数组")
    review_raw = Column(Text, nullable=True,
                        comment="LLM 完整原始输出，便于事后审计跨模型评分一致性")
    review_sample_scores = Column(Text, nullable=True,
                                   comment="多次采样各次分数 JSON 数组，反映评分波动幅度")
    summary_top_n = Column(Integer, nullable=True, default=5,
                            comment="生成时使用的 top_n")

    # 状态
    llm_status = Column(String(20), nullable=False, default="pending",
                         comment="pending/success/failed/skipped")
    llm_error = Column(Text, nullable=True, comment="LLM 失败原因")

    __table_args__ = (
        UniqueConstraint("author_email", "stat_date",
                          name="uq_employee_efficiency_email_date"),
        Index("idx_employee_efficiency_stat_date", "stat_date"),
        Index("idx_employee_efficiency_email_date",
              "author_email", "stat_date"),
    )

    def __repr__(self):
        return (f"<EmployeeEfficiencyDaily(email='{self.author_email}', "
                f"date={self.stat_date}, score={self.review_score})>")
