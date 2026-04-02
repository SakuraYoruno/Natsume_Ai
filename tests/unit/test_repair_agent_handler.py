from __future__ import annotations

from host_brain.app.handlers import repair_agent_handler as mod


def test_repair_browser_backend_attempts_install_and_succeeds(monkeypatch) -> None:
    commands = []
    state = {"agent": False, "playwright": False}

    def fake_which(name):
        if name in {"npm", "npm.cmd"}:
            return "C:/npm.cmd"
        if name in {"agent-browser", "agent-browser.cmd"} and state["agent"]:
            return "C:/agent-browser.cmd"
        return None

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        joined = " ".join(command)
        if 'agent-browser' in joined and 'install' not in joined:
            state['agent'] = True
        if 'pip install playwright' in joined or ('-m' in command and 'playwright' in command and 'install' in command):
            state['playwright'] = True
        return type('CP', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

    monkeypatch.setattr(mod.shutil, 'which', fake_which)
    monkeypatch.setattr(mod.subprocess, 'run', fake_run)
    monkeypatch.setattr(mod.importlib.util, 'find_spec', lambda name: object() if (name == 'playwright' and state['playwright']) else None)
    monkeypatch.setattr(mod, '_resolve_python_executable', lambda: 'python')

    result = mod._repair_browser_backend()

    assert result['success'] is True
    assert any('agent-browser' in ' '.join(cmd) for cmd in commands)


def test_repair_agent_handler_returns_success_summary(monkeypatch) -> None:
    monkeypatch.setattr(mod, '_build_repair_result', lambda **_kwargs: {
        'success': True,
        'summary': '浏览器后端已经补齐，可以重试原任务。',
        'detail': 'ok',
        'actions': [],
        'ok_actions': 0,
    })

    out = mod.repair_agent_handler({'user_input': '打开网页', 'request_id': 'req-001'}, {'request_id': 'req-001', 'previous_agent_result': 'BROWSER_UNAVAILABLE'})

    assert out['react_scratchpad']['final_answer'] == '浏览器后端已经补齐，可以重试原任务。'
    assert out['repair_agent_trace']['success'] is True
