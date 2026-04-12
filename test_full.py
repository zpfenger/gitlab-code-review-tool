import requests
import json

BASE = 'http://127.0.0.1:5001'
s = requests.Session()

# 登录
r = s.post(f'{BASE}/api/auth/login', json={'username':'admin','password':'admin123'})
print('===== 登录 =====')
print(r.status_code, r.json())

# 查看项目列表
print('\n===== 项目列表 =====')
r = s.get(f'{BASE}/api/projects')
projects = r.json()
for p in projects:
    print(f"  [{p['id']}] {p['name']} - is_active={p['is_active']}")

# 查看设置
print('\n===== 系统设置 =====')
r = s.get(f'{BASE}/api/settings')
settings = r.json()
print(f"  LLM API URL: {settings.get('llm_api_url', '未配置')}")
print(f"  LLM 模型: {settings.get('llm_model', '未配置')}")
print(f"  GitLab URL: {settings.get('global_gitlab_url', '未配置')}")
print(f"  调度器启用: {settings.get('scheduler_enabled')}")

# 查看日志
print('\n===== 最近任务日志 =====')
r = s.get(f'{BASE}/api/logs?page=1&per_page=5')
logs_data = r.json()
logs = logs_data.get('logs', logs_data if isinstance(logs_data, list) else [])
for log in logs[:5]:
    print(f"  [{log.get('id')}] {log.get('project_name','?')} | {log.get('task_type','?')} | {log.get('status','?')} | {log.get('start_time','?')}")

# 手动触发日报（使用 API）
print('\n===== 手动触发日报（2026-03-27）=====')
r = s.post(f'{BASE}/api/tasks/run', json={
    'task_type': 'daily',
    'date': '2026-03-27',
    'project_ids': None
})
print(r.status_code, r.text[:500])
