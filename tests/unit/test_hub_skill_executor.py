from __future__ import annotations

from types import SimpleNamespace

from host_brain.skills.impl.hub_skill_executor import execute_hub_skill


def test_execute_hub_skill_install_activates(monkeypatch) -> None:
    monkeypatch.setattr('host_brain.skills.impl.hub_skill_executor.install_skill', lambda source, force=False: SimpleNamespace(success=True, name='demo_skill', version='1.0.0', source=source, install_dir='D:/skills/demo_skill', detail='installed ok', error=None))
    monkeypatch.setattr('host_brain.app.tool_routes._attempt_activate_hub_skill', lambda install_dir, skill_name: (True, ['demo_skill'], True, True, 2, 'skill installed, auto-repaired, and activated'))

    result = execute_hub_skill({'action': 'install', 'source': 'github:owner/repo/demo_skill', 'force': False, 'activate_now': True}, {})

    assert result['status'] == 'OK'
    assert result['detail']['activated'] is True
    assert result['detail']['repair_attempted'] is True


def test_execute_hub_skill_search_returns_entries(monkeypatch) -> None:
    monkeypatch.setattr('host_brain.skills.impl.hub_skill_executor.search_remote_skills_with_status', lambda query: SimpleNamespace(results=[SimpleNamespace(name='weather', version='1.0.0', description='weather', author='tester', tags=['weather'])], registry_url='https://example.com/registry.json', error=None))

    result = execute_hub_skill({'action': 'search', 'query': 'weather'}, {})

    assert result['status'] == 'OK'
    assert result['detail']['total'] == 1
    assert result['detail']['skills'][0]['name'] == 'weather'


def test_execute_hub_skill_list_returns_installed(monkeypatch) -> None:
    monkeypatch.setattr('host_brain.skills.impl.hub_skill_executor.list_installed_skills', lambda: [{'name': 'demo_skill'}])

    result = execute_hub_skill({'action': 'list'}, {})

    assert result['status'] == 'OK'
    assert result['detail']['total'] == 1


def test_execute_hub_skill_remove_refreshes_registry(monkeypatch) -> None:
    called = {'refresh': 0}
    monkeypatch.setattr('host_brain.skills.impl.hub_skill_executor.remove_skill', lambda skill_name: SimpleNamespace(success=True, name=skill_name, detail='removed', error=None))
    monkeypatch.setattr('host_brain.app.dependencies.refresh_skill_registry', lambda: called.__setitem__('refresh', called['refresh'] + 1) or None)

    result = execute_hub_skill({'action': 'remove', 'skill_name': 'demo_skill'}, {})

    assert result['status'] == 'OK'
    assert called['refresh'] == 1





def test_parse_skill_md_accepts_utf8_bom(tmp_path) -> None:
    from host_brain.skills.hub.manifest_parser import parse_skill_md

    skill_md = tmp_path / 'SKILL.md'
    skill_md.write_text(
        chr(0xfeff) +
        "---\n"
        "name: \"bom-skill\"\n"
        "version: \"0.1.0\"\n"
        "description: \"bom\"\n"
        "author: \"tester\"\n"
        "clearance: LOW\n"
        "risk_level: 1\n"
        "scope: host\n"
        "reversible: true\n"
        "require_confirm: false\n"
        "scheduler_allowed: true\n"
        "tool: web_search\n"
        "---\n\n"
        "# BOM Skill\n",
        encoding='utf-8',
    )

    manifest = parse_skill_md(skill_md)

    assert manifest.name == 'bom-skill'
    assert manifest.tool == 'web_search'



def test_execute_hub_skill_search_reports_registry_error(monkeypatch) -> None:
    monkeypatch.setattr(
        'host_brain.skills.impl.hub_skill_executor.search_remote_skills_with_status',
        lambda query: SimpleNamespace(results=[], registry_url='https://example.com/registry.json', error='HTTP Error 404: Not Found'),
    )

    result = execute_hub_skill({'action': 'search', 'query': 'weather'}, {})

    assert result['status'] == 'OK'
    assert result['detail']['total'] == 0
    assert result['detail']['registry_error'] == 'HTTP Error 404: Not Found'



def test_parse_github_source_supports_skillshub_alias() -> None:
    from host_brain.skills.hub.hub_installer import _parse_github_source

    parsed = _parse_github_source('skillshub:anthropics/skills/frontend-design')

    assert parsed == ('anthropics/skills', 'main', 'frontend-design')



def test_search_remote_skills_with_status_without_registry_config_uses_clawhub_fallback(monkeypatch) -> None:
    from host_brain.skills.hub.hub_installer import SearchResponse, SearchResult, search_remote_skills_with_status

    monkeypatch.delenv('HOST_SKILL_HUB_REGISTRY_URL', raising=False)
    monkeypatch.setattr(
        'host_brain.skills.hub.hub_installer._search_skills_via_clawhub_cli',
        lambda query, skills_dir=None: SearchResponse(
            results=[SearchResult(name='weather-like-test', version='', description='Weather Like Test', author='ClawHub', tags=['clawhub'])],
            registry_url='https://clawhub.ai',
            error=None,
            discovery_sources=[{'name': 'ClawHub', 'url': 'https://clawhub.ai'}],
        ),
    )

    result = search_remote_skills_with_status('weather')

    assert len(result.results) == 1
    assert result.results[0].name == 'weather-like-test'
    assert result.registry_url == 'https://clawhub.ai'
    assert result.error is None




def test_parse_clawhub_search_output() -> None:
    from host_brain.skills.hub.hub_installer import _parse_clawhub_search_output

    rows = _parse_clawhub_search_output(
        "weather-pollen  Weather Pollen  (3.554)\n"
        "weather-1-0-0  Weather 1.0.0  (3.512)\n"
        "- Searching\n"
    )

    assert [row.name for row in rows] == ['weather-pollen', 'weather-1-0-0']
    assert rows[0].description == 'Weather Pollen'


def test_install_skill_clawhub_source_uses_cli_bridge(monkeypatch, tmp_path) -> None:
    from host_brain.skills.hub.hub_installer import install_skill

    hub_dir = tmp_path / 'skills'
    installed_dir = hub_dir / 'weather-like-test'
    installed_dir.mkdir(parents=True)
    (installed_dir / 'SKILL.md').write_text(
        "---\n"
        "name: \"weather-like-test\"\n"
        "version: \"0.1.0\"\n"
        "description: \"Weather Like Test\"\n"
        "author: \"tester\"\n"
        "clearance: LOW\n"
        "risk_level: 1\n"
        "scope: host\n"
        "reversible: true\n"
        "require_confirm: false\n"
        "scheduler_allowed: true\n"
        "tool: web_search\n"
        "---\n",
        encoding='utf-8',
    )

    class _Completed:
        returncode = 0
        stdout = 'installed ok'
        stderr = ''

    monkeypatch.setattr('host_brain.skills.hub.hub_installer._run_clawhub_cli', lambda *args, **kwargs: _Completed())

    result = install_skill('clawhub:weather-like-test', skills_dir=hub_dir, force=False)

    assert result.success is True
    assert result.name == 'weather-like-test'
    assert result.source == 'clawhub:weather-like-test'
    assert result.install_dir.endswith('weather-like-test')



def test_execute_hub_skill_install_keeps_success_when_activation_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        'host_brain.skills.impl.hub_skill_executor.install_skill',
        lambda source, force=False: SimpleNamespace(success=True, name='demo_skill', version='1.0.0', source=source, install_dir='D:/skills/demo_skill', detail='installed ok', error=None),
    )

    def _raise_activation(*args, **kwargs):  # noqa: ANN001
        raise NameError('os is not defined')

    monkeypatch.setattr('host_brain.app.tool_routes._attempt_activate_hub_skill', _raise_activation)

    result = execute_hub_skill({'action': 'install', 'source': 'clawhub:demo_skill', 'force': False, 'activate_now': True}, {})

    assert result['status'] == 'OK'
    assert result['detail']['success'] is True
    assert result['detail']['activated'] is False
    assert '激活失败' in result['detail']['message']
