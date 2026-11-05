"""停止任务接口：区分「只杀当前运行」与「同时关闭定时」。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import dependencies as deps
from src.api.routes import tasks as tasks_route
from src.domain.models.task import TaskCreate
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
from src.services.task_service import TaskService


class FakeProcessService:
    def __init__(self, running: bool = False):
        self.running = running
        self.stopped: list[int] = []

    async def stop_task(self, task_id: int) -> bool:
        self.stopped.append(task_id)
        return self.running


class FakeSchedulerService:
    def __init__(self):
        self.reload_calls = 0

    async def reload_jobs(self, _tasks):
        self.reload_calls += 1

    def get_next_run_time(self, _task_id):
        return None


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    repository = SqliteTaskRepository(db_path=str(db_path), legacy_config_file=None)
    task_service = TaskService(repository)
    process_service = FakeProcessService(running=True)
    scheduler_service = FakeSchedulerService()

    app = FastAPI()
    app.include_router(tasks_route.router)
    app.dependency_overrides[deps.get_task_service] = lambda: task_service
    app.dependency_overrides[deps.get_process_service] = lambda: process_service
    app.dependency_overrides[deps.get_scheduler_service] = lambda: scheduler_service

    context = {
        "client": TestClient(app),
        "service": task_service,
        "process": process_service,
        "scheduler": scheduler_service,
    }
    return context


def _create_kongfz_task(service: TaskService, **overrides):
    payload = TaskCreate(
        task_name="孔网-红楼梦",
        keyword="红楼梦",
        platform="kongfz",
        latest_count=10,
        cron="* * * * *",
        notify_all_new=True,
        decision_mode="keyword",
        keyword_rules=["红楼梦"],
        **overrides,
    )

    async def run():
        return await service.create_task(payload)

    import asyncio

    return asyncio.run(run())


def test_stop_with_disable_closes_schedule(client):
    task = _create_kongfz_task(client["service"])

    response = client["client"].post(f"/api/tasks/stop/{task.id}?disable=true")

    assert response.status_code == 200
    body = response.json()
    assert body["disabled"] is True
    assert body["enabled"] is False
    assert "定时已关闭" in body["message"]
    assert client["process"].stopped == [task.id]
    assert client["scheduler"].reload_calls >= 1

    import asyncio

    reloaded = asyncio.run(client["service"].get_task(task.id))
    assert reloaded.enabled is False


def test_stop_without_disable_keeps_schedule(client):
    task = _create_kongfz_task(client["service"])

    response = client["client"].post(f"/api/tasks/stop/{task.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["disabled"] is False
    assert body["enabled"] is True
    assert "定时任务仍保留" in body["message"]

    import asyncio

    reloaded = asyncio.run(client["service"].get_task(task.id))
    assert reloaded.enabled is True


def test_stop_when_nothing_running_reports_honestly(client):
    client["process"].running = False
    task = _create_kongfz_task(client["service"])

    response = client["client"].post(f"/api/tasks/stop/{task.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["stopped_process"] is False
    assert "没有正在运行的进程" in body["message"]
    # 不能谎报"已停止"：明确提示定时仍会触发
    assert "定时任务仍会继续触发" in body["message"]