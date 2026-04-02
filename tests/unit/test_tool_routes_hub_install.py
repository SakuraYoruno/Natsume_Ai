from __future__ import annotations

from types import SimpleNamespace

from host_brain.app import tool_routes


def test_install_hub_skill_route_refreshes_registry(monkeypatch) -> None:
    monkeypatch.setattr(tool_routes, 'install_hub_skill', lambda source, force=False: SimpleNamespace(success=True, name='demo_skill', version='1.0.0', source=source, install_dir='D:/skills/demo_skill', detail='installed ok', error=None))
    monkeypatch.setattr(tool_routes, '_attempt_activate_hub_skill', lambda install_dir, skill_name: (True, ['demo_skill', 'web_search'], False, False, 0, 'skill installed and registry refreshed'))

    response = __import__('asyncio').run(tool_routes.install_hub_skill_route(tool_routes.HubSkillInstallRequest(source='github:owner/repo/demo_skill', force=False, activate_now=True)))

    assert response.status == 'INSTALLED'
    assert response.activated is True
    assert response.repair_attempted is False
    assert 'demo_skill' in response.active_skill_names


def test_list_hub_skills_route_returns_entries(monkeypatch) -> None:
    monkeypatch.setattr(tool_routes, 'list_installed_hub_skills', lambda: [
        {'name': 'demo_skill', 'version': '1.0.0', 'description': 'demo', 'author': 'tester', 'source': 'github', 'installed_at': 'now', 'dir': 'D:/skills/demo_skill'}
    ])

    response = __import__('asyncio').run(tool_routes.list_hub_skills())

    assert response.total == 1
    assert response.skills[0].name == 'demo_skill'


def test_search_hub_skills_route_returns_remote_entries(monkeypatch) -> None:
    monkeypatch.setattr(tool_routes, 'search_remote_skills', lambda query: [SimpleNamespace(name='demo_skill', version='1.0.0', description='demo', author='tester', tags=['demo'])])

    response = __import__('asyncio').run(tool_routes.search_hub_skills('demo'))

    assert response.total == 1
    assert response.skills[0].source == 'remote'


def test_remove_hub_skill_route_refreshes_registry_on_success(monkeypatch) -> None:
    called = {'refresh': 0}
    monkeypatch.setattr(tool_routes, 'remove_hub_skill', lambda skill_name: SimpleNamespace(success=True, name=skill_name, detail='removed', error=None))
    monkeypatch.setattr('host_brain.app.dependencies.refresh_skill_registry', lambda: called.__setitem__('refresh', called['refresh'] + 1) or None)

    response = __import__('asyncio').run(tool_routes.remove_hub_skill_route('demo_skill'))

    assert response.status == 'REMOVED'
    assert called['refresh'] == 1



def test_install_hub_skill_route_reports_auto_repair(monkeypatch) -> None:
    monkeypatch.setattr(tool_routes, 'install_hub_skill', lambda source, force=False: SimpleNamespace(success=True, name='demo_skill', version='1.0.0', source=source, install_dir='D:/skills/demo_skill', detail='installed ok', error=None))
    monkeypatch.setattr(tool_routes, '_attempt_activate_hub_skill', lambda install_dir, skill_name: (True, ['demo_skill'], True, True, 2, 'skill installed, auto-repaired, and activated'))

    response = __import__('asyncio').run(tool_routes.install_hub_skill_route(tool_routes.HubSkillInstallRequest(source='github:owner/repo/demo_skill', force=False, activate_now=True)))

    assert response.status == 'INSTALLED'
    assert response.activated is True
    assert response.repair_attempted is True
    assert response.repair_succeeded is True
    assert response.repair_actions == 2
