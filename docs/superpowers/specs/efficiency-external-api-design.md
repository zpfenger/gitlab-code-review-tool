# 人员能效明细数据外部接口 - 设计文档

> **版本**: v1.0
> **日期**: 2026-06-04
> **作者**: 张鹏
> **状态**: 设计评审中

---

## 一、需求概述

### 1.1 背景

当前 `code-review-tool` 系统已完成人员能效数据的采集、LLM评分和存储（`employee_efficiency_daily` 表），包含代码提交统计、评分、评分简述和工作总结等数据。现需要将这些数据同步到 HR 系统的工作日报模块中，供管理人员查看研发人员的代码产出情况。

### 1.2 核心目标

1. 在 `code-review-tool` 中新增外部 API 接口，供 HR 系统调用获取能效数据
2. 在 HR 系统的工作日报（`ebm_daily`）中新增代码评审相关字段
3. HR 系统通过定时任务自动获取前一天的能效数据
4. 仅对研发体系（`employee_system_cd == "06"`）的员工生效
5. 当工作日报不存在时自动创建；已有工作内容时不覆盖

### 1.3 涉及系统

| 系统 | 技术栈 | 职责 |
|------|--------|------|
| code-review-tool | Python / FastAPI / SQLAlchemy / SQLite | 数据生产方，暴露外部API |
| HR System | Java / Spring MVC / JPA / MySQL | 数据消费方，定时获取并展示 |

---

## 二、整体架构

### 2.1 数据流转时序

```
Day N 凌晨:

00:30  code-review-tool: APScheduler触发每日任务
       ↓
00:31  code-review-tool: 遍历项目，拉取Day N-1的commits
       ↓
01:00  code-review-tool: LLM审查+评分（耗时取决于提交量）
       ↓
01:30  code-review-tool: 写入employee_efficiency_daily表
       ↓
02:00  code-review-tool: 任务完成，数据就绪
       ↓
03:00  HR系统: 定时任务触发
       ↓
03:01  HR系统: GET /api/external/efficiency/daily?date=Day N-1
       ↓
03:02  code-review-tool: 返回Day N-1的能效数据
       ↓
03:03  HR系统: 遍历数据 → 匹配员工 → 过滤06体系 → 写入ebm_daily
       ↓
03:05  HR系统: 同步完成
```

### 2.2 架构图

```
┌──────────────────────────┐              ┌──────────────────────────┐
│    code-review-tool      │              │       HR System          │
│   (Python/FastAPI)       │              │    (Java/Spring MVC)     │
├──────────────────────────┤              ├──────────────────────────┤
│                          │              │                          │
│ ① 00:30 定时任务触发      │              │ ④ 03:00 定时任务触发      │
│    ↓                     │              │    ↓                     │
│ ② 代码审查 + LLM评分     │              │ ⑤ 调用外部API获取数据     │
│    ↓                     │    HTTP      │    ↓                     │
│ ③ 写入employee_efficiency│◄─────────────│ ⑥ 匹配员工(06研发体系)   │
│    _daily表 + 暴露API    │  API Key认证  │    ↓                     │
│                          │              │ ⑦ 写入ebm_daily表        │
│                          │              │    ↓                     │
│                          │              │ ⑧ 前端UI展示             │
└──────────────────────────┘              └──────────────────────────┘
```

---

## 三、code-review-tool 改动清单

### 3.1 数据库改动

`Settings` 模型新增字段：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `external_api_key` | String(200) | 外部API访问密钥（Fernet加密存储） |

### 3.2 新增外部API

**文件**: `app/api/external.py`（新建）

| 方法 | 路径 | 认证方式 | 说明 |
|------|------|----------|------|
| `GET` | `/api/external/efficiency/daily` | API Key (`X-API-Key` Header) | 获取指定日期人员能效数据 |

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| `X-API-Key` | Header | String | 是 | API访问密钥 |
| `date` | Query | String (YYYY-MM-DD) | 否 | 统计日期，默认前一天 |

#### 成功响应

```json
{
  "success": true,
  "data": {
    "date": "2026-06-03",
    "generated_at": "2026-06-03T01:15:30",
    "llm_status": "success",
    "items": [
      {
        "author_email": "zhangsan@company.com",
        "author_name": "张三",
        "commits_count": 5,
        "additions": 120,
        "deletions": 30,
        "files_changed": 8,
        "new_files": 2,
        "deleted_files": 0,
        "projects_involved": ["project-a", "project-b"],
        "review_score": 85,
        "review_grade": "良好",
        "review_summary": "代码质量较好，逻辑清晰，建议增加单元测试覆盖",
        "work_summary": [
          "完成用户登录模块的重构",
          "修复订单超时处理bug",
          "优化数据库查询性能"
        ],
        "llm_status": "success"
      }
    ]
  }
}
```

#### 数据未就绪响应

```json
{
  "success": true,
  "data": {
    "date": "2026-06-03",
    "llm_status": "pending",
    "message": "能效数据尚未生成，请稍后重试",
    "items": []
  }
}
```

#### 错误响应

```json
// API Key无效
{
  "success": false,
  "error": {
    "code": 401,
    "message": "Invalid API Key"
  }
}

// 日期格式错误
{
  "success": false,
  "error": {
    "code": 400,
    "message": "日期格式错误，应为 YYYY-MM-DD"
  }
}
```

### 3.3 修改文件清单

| 文件路径 | 改动类型 | 说明 |
|----------|----------|------|
| `app/models/settings.py` | 修改 | 新增 `external_api_key` 字段 |
| `app/api/external.py` | **新建** | 外部API路由（API Key认证 + 数据查询） |
| `app/main.py` | 修改 | 注册外部API路由 |
| `app/api/settings.py` | 修改 | 设置API增加API Key管理接口 |
| `app/templates/settings.html` | 修改 | 设置页面UI增加API Key配置区域 |

### 3.4 安全设计

- API Key 使用 Fernet 对称加密存储在 `settings` 表中
- 请求验证：检查 `X-API-Key` Header 与数据库中存储的密钥是否匹配
- 仅返回 `llm_status == "success"` 的数据
- 响应中不暴露内部错误堆栈

---

## 四、HR系统改动清单

### 4.1 数据库表扩展

`ebm_daily` 表新增字段：

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `code_commits_count` | INT | NULL | 提交次数 |
| `code_additions` | INT | NULL | 新增行数 |
| `code_deletions` | INT | NULL | 删除行数 |
| `code_files_changed` | INT | NULL | 涉及文件数 |
| `code_score` | INT | NULL | 代码评分(0-100) |
| `code_grade` | VARCHAR(10) | NULL | 评分等级（优秀/良好/一般/待改进） |
| `code_summary` | VARCHAR(500) | NULL | 评分简述 |
| `code_work_summary` | TEXT | NULL | 代码工作总结(JSON数组) |
| `code_review_sync_time` | DATETIME | NULL | 数据同步时间 |

### 4.2 实体类修改

**文件**: `com.renruihr.ebm.entity.Daily`

```java
// ========== 代码评审数据字段 ==========

/**
 * 代码提交次数
 */
@Column(name = "code_commits_count")
private Integer codeCommitsCount;

/**
 * 代码新增行数
 */
@Column(name = "code_additions")
private Integer codeAdditions;

/**
 * 代码删除行数
 */
@Column(name = "code_deletions")
private Integer codeDeletions;

/**
 * 代码涉及文件数
 */
@Column(name = "code_files_changed")
private Integer codeFilesChanged;

/**
 * 代码评分(0-100)
 */
@Column(name = "code_score")
private Integer codeScore;

/**
 * 评分等级
 */
@Length(max = 10)
@Column(name = "code_grade", length = 10)
private String codeGrade;

/**
 * 评分简述
 */
@Length(max = 500)
@Column(name = "code_summary", length = 500)
private String codeSummary;

/**
 * 代码工作总结(JSON数组)
 */
@Column(name = "code_work_summary", columnDefinition = "TEXT")
private String codeWorkSummary;

/**
 * 代码评审数据同步时间
 */
@Column(name = "code_review_sync_time")
@DateTimeFormat(pattern = CmnConsts.DATE_TIME_FORMAT)
@JsonFormat(pattern = CmnConsts.DATE_TIME_FORMAT, timezone = "GMT+8")
private Date codeReviewSyncTime;
```

### 4.3 新增定时任务

**文件**: `com.renruihr.ebm.scheduler.DailyCodeReviewSyncTask`（新建）

```java
@Component
public class DailyCodeReviewSyncTask {

    private static final Logger log = LoggerFactory.getLogger(DailyCodeReviewSyncTask.class);

    @Autowired
    private DailyService dailyService;

    @Autowired
    private EmployeeService employeeService;

    @Autowired
    private SystemConfigService configService;

    /**
     * 每天03:00同步code-review-tool的能效数据
     */
    @Scheduled(cron = "0 0 3 * * ?")
    public void syncCodeReviewData() {
        Date yesterday = DateUtils.addDays(new Date(), -1);
        String dateStr = DateUtils.formatDate(yesterday, "yyyy-MM-dd");

        log.info("开始同步代码评审数据，日期: {}", dateStr);

        // 1. 获取配置
        String apiUrl = configService.getValue("code.review.api.url");
        String apiKey = configService.getValue("code.review.api.key");

        if (StringUtils.isEmpty(apiUrl) || StringUtils.isEmpty(apiKey)) {
            log.warn("代码评审API配置未设置，跳过同步");
            return;
        }

        // 2. 调用code-review-tool API
        JSONObject response = callCodeReviewApi(apiUrl, apiKey, dateStr);
        if (response == null) return;

        // 3. 检查数据状态
        JSONObject data = response.getJSONObject("data");
        String llmStatus = data.getString("llm_status");

        if (!"success".equals(llmStatus)) {
            log.warn("能效数据尚未生成(llm_status={})，跳过本次同步", llmStatus);
            return;
        }

        // 4. 遍历处理
        JSONArray items = data.getJSONArray("items");
        int successCount = 0;
        int skipCount = 0;

        for (Object item : items) {
            try {
                boolean processed = processOneRecord((JSONObject) item, yesterday);
                if (processed) successCount++;
                else skipCount++;
            } catch (Exception e) {
                log.error("处理记录失败: {}", ((JSONObject) item).getString("author_email"), e);
                skipCount++;
            }
        }

        log.info("代码评审数据同步完成: 成功={}, 跳过={}", successCount, skipCount);
    }

    /**
     * 处理单条能效数据
     * @return true=成功处理, false=跳过
     */
    private boolean processOneRecord(JSONObject effData, Date targetDate) {
        String email = effData.getString("author_email");

        // 1. 通过company_email匹配HR员工
        Employee employee = employeeService.findByCompanyEmail(email);
        if (employee == null) {
            log.debug("未匹配到员工: {}", email);
            return false;
        }

        // 2. 判断是否为06研发体系
        if (!"06".equals(employee.getEmployeeSystemCd())) {
            log.debug("员工非研发体系(06)，跳过: {} - {}", email, employee.getEmployeeSystemCd());
            return false;
        }

        // 3. 查找或创建日报记录
        Daily daily = dailyService.findByEmployeeAndDate(employee.getUid(), targetDate);
        boolean isNew = (daily == null);

        if (isNew) {
            daily = new Daily();
            daily.setEmployeeUid(employee.getUid());
            daily.setDailyDate(targetDate);
            daily.setWorkContent("");
            daily.setDailySourceCd("03");  // 系统来源
            // 设置其他必要默认值...
        }

        // 4. 填充代码评审字段
        daily.setCodeCommitsCount(effData.getIntValue("commits_count"));
        daily.setCodeAdditions(effData.getIntValue("additions"));
        daily.setCodeDeletions(effData.getIntValue("deletions"));
        daily.setCodeFilesChanged(effData.getIntValue("files_changed"));
        daily.setCodeScore(effData.getIntValue("review_score"));
        daily.setCodeGrade(effData.getString("review_grade"));
        daily.setCodeSummary(effData.getString("review_summary"));
        daily.setCodeWorkSummary(effData.getJSONArray("work_summary").toJSONString());
        daily.setCodeReviewSyncTime(new Date());

        // 5. 如果工作内容为空，填充代码工作总结
        if (isNew || StringUtils.isEmpty(daily.getWorkContent())) {
            JSONArray workSummary = effData.getJSONArray("work_summary");
            daily.setWorkContent(formatWorkSummary(workSummary));
        }

        // 6. 保存
        dailyService.save(daily);
        return true;
    }

    /**
     * 将工作摘要JSON数组格式化为编号列表
     * 输入: ["完成登录模块重构", "修复超时bug"]
     * 输出: "1. 完成登录模块重构\n2. 修复超时bug"
     */
    private String formatWorkSummary(JSONArray summary) {
        if (summary == null || summary.isEmpty()) return "";

        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < summary.size(); i++) {
            if (i > 0) sb.append("\n");
            sb.append(i + 1).append(". ").append(summary.getString(i));
        }
        return sb.toString();
    }

    private JSONObject callCodeReviewApi(String apiUrl, String apiKey, String dateStr) {
        try {
            String url = apiUrl + "?date=" + dateStr;
            // HTTP GET with X-API-Key header
            // 使用项目现有的HTTP工具类
            // 返回JSON响应
            return HttpUtils.get(url, Map.of("X-API-Key", apiKey));
        } catch (Exception e) {
            log.error("调用code-review-tool API失败", e);
            return null;
        }
    }
}
```

### 4.4 员工匹配策略

```
code-review-tool: author_email (如 zhangsan@company.com)
         ↓ 匹配
HR系统: hr_employee.company_email
         ↓ 过滤
hr_employee.employee_system_cd == "06" (研发体系)
```

### 4.5 日报处理逻辑

```
对于每条能效数据:
  1. 按 company_email 查找员工
  2. 员工不存在 → 跳过，记录日志
  3. 员工体系不是06 → 跳过
  4. 查找当天日报记录
     - 不存在 → 新建日报，work_content初始为空
     - 已存在 → 保留现有work_content
  5. 填充代码评审字段（code_* 字段）
  6. work_content为空时 → 用格式化的工作总结填充
  7. 保存
```

### 4.6 前端UI修改

#### 4.6.1 个人日报页面（DailyList.jsp）

在日报卡片中新增代码评审数据展示区域：

```jsp
<%-- 代码评审数据展示（仅研发体系06且有数据时显示） --%>
<c:if test="${daily.employeeSystemCd == '06' && daily.codeScore != null}">
<div class="code-review-section" style="margin-top: 10px; padding: 12px; background: #f8f9fa; border-radius: 6px; border-left: 4px solid #4CAF50;">
    <div style="font-weight: bold; margin-bottom: 8px; color: #333;">
        📊 代码评审数据
    </div>
    <div style="display: flex; gap: 16px; margin-bottom: 8px; font-size: 13px; color: #666;">
        <span>提交: <strong>${daily.codeCommitsCount}</strong>次</span>
        <span>新增: <strong>${daily.codeAdditions}</strong>行</span>
        <span>删除: <strong>${daily.codeDeletions}</strong>行</span>
        <span>文件: <strong>${daily.codeFilesChanged}</strong>个</span>
    </div>
    <div style="margin-bottom: 8px;">
        <span style="font-size: 18px; font-weight: bold; color: #4CAF50;">${daily.codeScore}</span>
        <span style="font-size: 13px; color: #666;">分 (${daily.codeGrade})</span>
    </div>
    <c:if test="${not empty daily.codeSummary}">
        <div style="font-size: 13px; color: #555; margin-bottom: 4px;">
            ${daily.codeSummary}
        </div>
    </c:if>
</div>
</c:if>
```

#### 4.6.2 部门日报页面（DeptDailyList.jsp）

在部门日报表格中新增列（仅研发体系员工显示）：

| 列名 | 说明 |
|------|------|
| 代码提交 | `codeCommitsCount` |
| 代码评分 | `codeScore` + `codeGrade` |

#### 4.6.3 前端判断逻辑

```javascript
// DailyList.js - 判断是否显示代码评审区域
shouldShowCodeReview: function(daily) {
    return daily.employeeSystemCd === '06'
        && daily.codeScore != null
        && daily.codeScore > 0;
}
```

### 4.7 新增配置项

在系统参数表中新增：

| 参数键 | 说明 | 示例值 |
|--------|------|--------|
| `code.review.api.url` | code-review-tool API地址 | `http://10.0.0.1:8000/api/external/efficiency/daily` |
| `code.review.api.key` | API访问密钥 | `sk-xxxxxxxxxxxx` |

### 4.8 HR系统改动文件清单

| 文件路径 | 改动类型 | 说明 |
|----------|----------|------|
| `com.renruihr.ebm.entity.Daily` | 修改 | 新增9个代码评审字段 |
| `com.renruihr.ebm.scheduler.DailyCodeReviewSyncTask` | **新建** | 定时同步任务 |
| `com.renruihr.ebm.service.DailyService` | 修改 | 新增 `findByEmployeeAndDate()` 方法 |
| `com.renruihr.hr.service.EmployeeService` | 修改 | 新增 `findByCompanyEmail()` 方法 |
| `resources/.../DailyDsql.xml` | 修改 | 查询SQL增加新字段 |
| `WEB-INF/views/ebm/daily/DailyList.jsp` | 修改 | 展示代码评审数据 |
| `WEB-INF/views/ebm/daily/DeptDailyList.jsp` | 修改 | 部门日报展示代码评审列 |
| `WEB-INF/views/ebm/daily/DailyPopupInput.jsp` | 修改 | 编辑弹窗展示代码评审数据 |
| `static/js/ebm/daily/DailyList.js` | 修改 | 前端逻辑处理 |
| 系统参数配置 | 修改 | 新增code-review API配置 |

---

## 五、字段映射关系

| code-review-tool 字段 | HR 字段 | 转换逻辑 |
|----------------------|---------|----------|
| `author_email` | `hr_employee.company_email` | 用于员工匹配 |
| `stat_date` | `daily_date` | 日期对应 |
| `commits_count` | `code_commits_count` | 直接映射 |
| `additions` | `code_additions` | 直接映射 |
| `deletions` | `code_deletions` | 直接映射 |
| `files_changed` | `code_files_changed` | 直接映射 |
| `review_score` | `code_score` | 直接映射 |
| `review_grade` | `code_grade` | 直接映射 |
| `review_summary` | `code_summary` | 直接映射（截断至500字符） |
| `work_summary` | `code_work_summary` | JSON原样存储 |
| `work_summary` | `work_content` | 格式化为编号列表（仅work_content为空时） |

---

## 六、容错与异常处理

| 场景 | 处理方式 |
|------|----------|
| code-review任务未完成 | HR系统收到 `llm_status: "pending"`，记录日志跳过 |
| API调用失败（网络异常） | HR系统记录错误日志，支持手动触发重试 |
| API Key无效 | code-review-tool返回401，HR系统记录告警 |
| 邮箱匹配失败 | 记录未匹配的email到日志，供人工排查 |
| 员工非06体系 | 跳过，不处理 |
| ebm_daily记录已存在 | 仅更新代码评审字段，不覆盖工作内容 |
| ebm_daily记录不存在 | 自动创建，work_content用代码工作总结填充 |
| 日期格式错误 | code-review-tool返回400错误 |

---

## 七、时间协调策略

| 时间 | 系统 | 动作 |
|------|------|------|
| 00:30 | code-review-tool | 定时任务开始（审查+评分+写入efficiency表） |
| ~02:00 | code-review-tool | 任务完成（取决于项目数量和LLM响应速度） |
| 03:00 | HR系统 | 定时任务开始（调用code-review API获取数据） |
| ~03:05 | HR系统 | 数据同步完成 |

**容错机制**：
- 如果03:00时code-review-tool尚未完成，HR系统收到 `llm_status: "pending"` 后跳过
- 可配置重试策略：每30分钟重试一次，最多重试3次（可选）
- 支持管理员在HR系统手动触发同步

---

## 八、后续扩展预留

1. **月度汇总接口**：`GET /api/external/efficiency/monthly?year_month=2026-06`
2. **多体系支持**：参数化配置体系编码列表（不仅限于06）
3. **同步状态查询**：HR系统提供数据同步状态查询页面
4. **手动补同步**：HR系统管理员可手动指定日期触发同步
5. **数据校验**：定期校验两个系统数据一致性

---

## 九、测试要点

### 9.1 code-review-tool 测试

- [ ] API Key 认证机制正确性
- [ ] 日期参数校验（默认值、格式错误、未来日期）
- [ ] 返回数据格式正确性
- [ ] 数据未就绪时的响应
- [ ] 加密存储 API Key

### 9.2 HR系统测试

- [ ] 定时任务触发正确性
- [ ] API调用与响应解析
- [ ] 员工匹配逻辑（company_email）
- [ ] 06体系过滤逻辑
- [ ] 日报自动创建（记录不存在时）
- [ ] 不覆盖已有工作内容
- [ ] 工作总结格式化（编号列表）
- [ ] 前端UI展示（仅06体系显示）
- [ ] 异常场景处理

### 9.3 集成测试

- [ ] 端到端数据流转验证
- [ ] 时间协调验证
- [ ] 大数据量性能测试
- [ ] 网络异常恢复测试

---

## 十、设计审查记录

### 10.1 审查发现与决策

| # | 发现 | 决策 | 状态 |
|---|------|------|------|
| 1 | API Key 轮换机制缺失 | 在 code-review-tool 设置页面提供可配置的 Key 管理（显示/隐藏/自定义） | ✅ 已确认 |
| 2 | `dailySourceCd="03"` 未在字典中定义 | 在 HR 系统 EBM004 字典中新增"03=人效系统" | ✅ 已确认 |
| 3 | `_serialize()` 函数重复 | 外部 API 复用 `efficiency.py` 中的 `_serialize()` 函数 | ✅ 已确认 |
| 4 | 响应大小（200-500KB） | 当前设计足够，无需分页 | ✅ 已确认 |
| 5 | HR 同步任务测试 | 仅测试 code-review-tool，HR 系统由 HR 团队自行测试 | ✅ 已确认 |

### 10.2 测试计划（code-review-tool）

新增测试文件：`tests/test_api/test_external.py`

| 用例 | 输入 | 预期输出 | 优先级 |
|------|------|----------|--------|
| Valid API Key + data exists | Header: valid key, Query: valid date | 200 + items array | P1 |
| Invalid API Key | Header: invalid key | 401 Unauthorized | P1 |
| Missing API Key header | No header | 401 Unauthorized | P1 |
| Invalid date format | Query: "invalid-date" | 400 Bad Request | P2 |
| Default date | Query: (empty) | 200 + yesterday's data | P2 |
| Data not ready | llm_status=pending | 200 + empty items | P2 |
| No data for date | date with no records | 200 + empty items | P2 |
| _serialize() format | Check response fields | Matches efficiency API | P2 |

### 10.3 更新后的字段映射

| code-review-tool 字段 | HR 字段 | 转换逻辑 |
|----------------------|---------|----------|
| `author_email` | `hr_employee.company_email` | 用于员工匹配 |
| `stat_date` | `daily_date` | 日期对应 |
| `commits_count` | `code_commits_count` | 直接映射 |
| `additions` | `code_additions` | 直接映射 |
| `deletions` | `code_deletions` | 直接映射 |
| `files_changed` | `code_files_changed` | 直接映射 |
| `review_score` | `code_score` | 直接映射 |
| `review_grade` | `code_grade` | 直接映射 |
| `review_summary` | `code_summary` | 直接映射（截断至500字符） |
| `work_summary` | `code_work_summary` | JSON原样存储 |
| `work_summary` | `work_content` | 格式化为编号列表（仅work_content为空时） |
| — | `daily_source_cd` | 固定值 "03"（人效系统） |

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 4 issues, 0 critical gaps |

- **UNRESOLVED:** 0
- **VERDICT:** ENG CLEARED — ready to implement
