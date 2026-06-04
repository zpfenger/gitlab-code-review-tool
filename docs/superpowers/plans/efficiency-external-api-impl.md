# 人员能效明细数据外部接口 - 实施计划

> **版本**: v1.0
> **日期**: 2026-06-04
> **设计文档**: `docs/superpowers/specs/efficiency-external-api-design.md`
> **状态**: 待实施

---

## 一、实施概览

### 1.1 目标

在 `code-review-tool` 中新增外部 API 接口，供 HR 系统定时获取人员能效数据。

### 1.2 范围

仅限 `code-review-tool` 项目的改动。HR 系统由 HR 团队自行实施。

### 1.3 技术栈

- Python 3.9+
- FastAPI
- SQLAlchemy 2.0+
- pytest (测试框架)

---

## 二、任务清单

### Phase 1: 数据模型与配置

#### T1: Settings 模型新增 external_api_key 字段
- **文件**: `app/models/settings.py`
- **改动**: 新增 `external_api_key` 字段（String(200)，Fernet 加密）
- **依赖**: 无
- **验收**: 字段可正常读写，加密存储

#### T2: 数据库迁移支持
- **文件**: `app/database.py`
- **改动**: 确保 `_migrate_columns()` 支持新增字段自动迁移
- **依赖**: T1
- **验收**: 启动时自动创建新列

### Phase 2: 外部 API 实现

#### T3: 创建外部 API 路由模块
- **文件**: `app/api/external.py`（新建）
- **改动**:
  - 创建 `APIRouter(prefix="/api/external", tags=["external"])`
  - 实现 API Key 认证依赖项 `verify_api_key()`
  - 复用 `efficiency.py` 中的 `_serialize()` 函数
- **依赖**: T1
- **验收**: 路由可注册，认证逻辑正确

#### T4: 实现 GET /api/external/efficiency/daily 端点
- **文件**: `app/api/external.py`
- **改动**:
  - 接收 `date` 查询参数（可选，默认前一天）
  - 查询 `EmployeeEfficiencyDaily` 表
  - 检查 `llm_status`，非 success 返回空 items
  - 返回标准化响应格式
- **依赖**: T3
- **验收**: API 返回正确数据格式

#### T5: 注册外部 API 路由
- **文件**: `app/main.py`
- **改动**: 在路由注册区域添加 `external.router`
- **依赖**: T3
- **验收**: API 可通过 HTTP 访问

### Phase 3: 设置页面集成

#### T6: Settings API 增加 external_api_key 管理
- **文件**: `app/api/settings.py`
- **改动**:
  - GET 返回时包含 `external_api_key`（脱敏显示）
  - PUT 支持更新 `external_api_key`
  - POST 支持重新生成 API Key
- **依赖**: T1
- **验收**: API 可管理 Key

#### T7: 设置页面 UI 增加 API Key 配置区域
- **文件**: `app/templates/settings.html`
- **改动**:
  - 新增"外部接口"配置区域
  - 显示 API Key（默认脱敏，点击显示）
  - "重新生成"按钮
  - "复制"按钮
- **依赖**: T6
- **验收**: UI 可正常显示和操作

### Phase 4: 测试

#### T8: 创建外部 API 测试文件
- **文件**: `tests/test_api/test_external.py`（新建）
- **改动**:
  - 测试 fixtures: mock 数据库、mock Settings
  - 测试用例:
    1. Valid API Key + data exists → 200 + items
    2. Invalid API Key → 401
    3. Missing API Key header → 401
    4. Invalid date format → 400
    5. Default date (no param) → uses yesterday
    6. llm_status=pending → empty items
    7. No data for date → empty items
    8. Response format matches _serialize() output
- **依赖**: T3, T4
- **验收**: 所有测试通过，覆盖率 > 80%

---

## 三、实施顺序

```
T1 (Settings 字段)
  ↓
T2 (数据库迁移) ← 可并行
  ↓
T3 (外部 API 路由模块)
  ↓
T4 (GET /daily 端点)
  ↓
T5 (注册路由) ← 可与 T6 并行
  ↓
T6 (Settings API)
  ↓
T7 (设置页面 UI)
  ↓
T8 (测试)
```

**并行机会**:
- T2 与 T3 可并行（数据库迁移 vs 路由模块）
- T5 与 T6 可并行（注册路由 vs Settings API）

---

## 四、文件变更清单

| 文件路径 | 改动类型 | 说明 |
|----------|----------|------|
| `app/models/settings.py` | 修改 | 新增 `external_api_key` 字段 |
| `app/database.py` | 检查 | 确保迁移支持 |
| `app/api/external.py` | **新建** | 外部 API 路由 |
| `app/main.py` | 修改 | 注册路由 |
| `app/api/settings.py` | 修改 | Key 管理接口 |
| `app/templates/settings.html` | 修改 | Key 配置 UI |
| `tests/test_api/test_external.py` | **新建** | 测试用例 |

---

## 五、验收标准

### 5.1 功能验收

- [ ] `GET /api/external/efficiency/daily` 可正常访问
- [ ] API Key 认证机制工作正常
- [ ] 返回数据格式与设计文档一致
- [ ] `llm_status=pending` 时返回空 items
- [ ] 默认日期为前一天
- [ ] 设置页面可管理 API Key

### 5.2 测试验收

- [ ] 所有测试用例通过
- [ ] 代码覆盖率 > 80%
- [ ] 无 P1/P2 级别 bug

### 5.3 安全验收

- [ ] API Key 加密存储
- [ ] 无效 Key 返回 401
- [ ] 响应中不暴露内部错误堆栈
- [ ] 设置页面 Key 默认脱敏显示

---

## 六、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| API Key 泄露 | 高 | 支持重新生成 Key，日志记录访问 |
| 数据库迁移失败 | 中 | 自动迁移机制已存在，测试验证 |
| 响应格式变更 | 中 | 复用 _serialize()，格式契约文档化 |
| 性能问题 | 低 | 数据量可控（200-500人），无需分页 |

---

## 七、后续扩展

1. 月度汇总接口
2. 多体系支持
3. API 分页
4. 访问日志与审计
