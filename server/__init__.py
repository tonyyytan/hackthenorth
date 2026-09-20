"""Server package with lazy compatibility for existing ``import server`` callers."""


def __getattr__(name):
    import importlib
    implementation = importlib.import_module(".server", __name__)
    return getattr(implementation, name)
