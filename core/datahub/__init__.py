"""Data Hub: the admin-only "move everything to a new database" engine.

Export every piece of configuration and master data (offices, roles, ranks,
request types and their approval chains, memo routing, leave types, QA flows,
institutions, programmes, facilities, users ...) to Excel workbooks that use
natural keys (codes, names, e-mails, "chain | step position") instead of
database ids, then import them, with a preview, into any database.

    from core.datahub import engine
    data = engine.export_workbook(["everything"])        # bytes (.xlsx)
    report = engine.import_workbook(fileobj, apply=False)  # preview (rolled back)
    report = engine.import_workbook(fileobj, apply=True)   # one transaction

The catalogue of what is covered lives in core/datahub/registry.py.
"""
