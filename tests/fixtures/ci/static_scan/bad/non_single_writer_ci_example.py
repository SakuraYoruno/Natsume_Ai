def bad_write_path(client):
    # Static-scan fixture: write_bond_event must be called only in bond_dynamics.
    return client.write_bond_event({"event_id": "ci_violation"})
