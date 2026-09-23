"""
Unit tests for plugin_system.py.
"""

import sys
import shutil
import tempfile
import unittest
from pathlib import Path

from secret_rotator.plugin_system import (
    PluginRegistry,
    PluginLoader,
    PluginMetadata,
    register_provider,
    register_rotator,
)
from secret_rotator.providers.base import SecretProvider
from secret_rotator.rotators.base import SecretRotator


class TestPluginRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = PluginRegistry()

    def test_register_and_get_provider(self):
        class DummyProvider:
            pass

        self.registry.register_provider("dummy", DummyProvider)
        self.assertIs(self.registry.get_provider("dummy"), DummyProvider)

    def test_register_and_get_rotator(self):
        class DummyRotator:
            pass

        self.registry.register_rotator("dummy", DummyRotator)
        self.assertIs(self.registry.get_rotator("dummy"), DummyRotator)

    def test_register_and_get_notifier(self):
        class DummyNotifier:
            pass

        self.registry.register_notifier("dummy", DummyNotifier)
        self.assertIs(self.registry.get_notifier("dummy"), DummyNotifier)

    def test_register_validator_is_listed(self):
        class DummyValidator:
            pass

        self.registry.register_validator("dummy", DummyValidator)
        self.assertIn("dummy", self.registry.list_available_plugins()["validators"])

    def test_get_unknown_provider_returns_none(self):
        self.assertIsNone(self.registry.get_provider("does-not-exist"))

    def test_list_available_plugins_shape(self):
        listing = PluginRegistry().list_available_plugins()
        self.assertEqual(set(listing.keys()), {"providers", "rotators", "notifiers", "validators"})
        self.assertEqual(listing["providers"], [])

    def test_registering_same_name_twice_overwrites(self):
        class First:
            pass

        class Second:
            pass

        self.registry.register_provider("dummy", First)
        self.registry.register_provider("dummy", Second)
        self.assertIs(self.registry.get_provider("dummy"), Second)


class TestPluginMetadata(unittest.TestCase):
    def test_validate_config_all_required_present(self):
        meta = PluginMetadata(
            name="my_plugin",
            version="1.0",
            author="tester",
            description="desc",
            required_config=["host", "port"],
            optional_config={"timeout": 30},
        )
        ok, missing = meta.validate_config({"host": "x", "port": 5432})
        self.assertTrue(ok)
        self.assertEqual(missing, [])

    def test_validate_config_reports_missing_keys(self):
        meta = PluginMetadata(
            name="my_plugin",
            version="1.0",
            author="tester",
            description="desc",
            required_config=["host", "port", "username"],
            optional_config={},
        )
        ok, missing = meta.validate_config({"host": "x"})
        self.assertFalse(ok)
        self.assertEqual(set(missing), {"port", "username"})

    def test_validate_config_empty_requirements_always_valid(self):
        meta = PluginMetadata(
            name="my_plugin",
            version="1.0",
            author="tester",
            description="desc",
            required_config=[],
            optional_config={},
        )
        ok, missing = meta.validate_config({})
        self.assertTrue(ok)


class TestRegistrationDecorators(unittest.TestCase):
    def test_register_provider_decorator_sets_plugin_name(self):
        @register_provider("my_custom_provider")
        class MyProvider:
            pass

        self.assertEqual(MyProvider.plugin_name, "my_custom_provider")

    def test_register_rotator_decorator_sets_plugin_name(self):
        @register_rotator("my_custom_rotator")
        class MyRotator:
            pass

        self.assertEqual(MyRotator.plugin_name, "my_custom_rotator")

    def test_decorator_returns_the_original_class(self):
        @register_provider("x")
        class MyProvider:
            def marker(self):
                return "still works"

        self.assertEqual(MyProvider().marker(), "still works")


class TestIsValidPlugin(unittest.TestCase):
    def setUp(self):
        self.loader = PluginLoader(plugins_dir=tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.loader.plugins_dir, ignore_errors=True)

    def test_base_provider_class_itself_is_rejected(self):
        self.assertFalse(self.loader._is_valid_plugin(SecretProvider, "providers"))

    def test_base_rotator_class_itself_is_rejected(self):
        self.assertFalse(self.loader._is_valid_plugin(SecretRotator, "rotators"))

    def test_concrete_provider_subclass_is_accepted(self):
        class ConcreteProvider(SecretProvider):
            def get_secret(self, secret_id):
                return ""

            def update_secret(self, secret_id, new_value):
                return True

            def validate_connection(self):
                return True

        self.assertTrue(self.loader._is_valid_plugin(ConcreteProvider, "providers"))

    def test_abstract_subclass_missing_methods_is_rejected(self):
        class IncompleteProvider(SecretProvider):
            def get_secret(self, secret_id):
                return ""

            # update_secret / validate_connection intentionally missing
            # -> class stays abstract

        self.assertFalse(self.loader._is_valid_plugin(IncompleteProvider, "providers"))

    def test_unrelated_class_is_rejected_for_providers(self):
        class NotAProvider:
            pass

        self.assertFalse(self.loader._is_valid_plugin(NotAProvider, "providers"))


class TestPluginLoaderScaffold(unittest.TestCase):
    """First-run behavior: no plugins/ directory yet."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir, ignore_errors=True)
        self.plugins_dir = Path(self.test_dir) / "plugins"

    def test_missing_plugins_dir_is_created(self):
        loader = PluginLoader(plugins_dir=str(self.plugins_dir))
        loader.discover_and_load_plugins()
        self.assertTrue(self.plugins_dir.exists())

    def test_example_plugin_is_written_as_dot_example_not_live_py(self):
        """The scaffolded example must not have a live .py extension —
        otherwise a fresh install would auto-load unreviewed example
        code the very first time discovery runs."""
        loader = PluginLoader(plugins_dir=str(self.plugins_dir))
        loader.discover_and_load_plugins()

        example = self.plugins_dir / "providers" / "example_custom_provider.py.example"
        self.assertTrue(example.exists())
        self.assertEqual(loader.registry.list_available_plugins()["providers"], [])


class TestPluginLoaderDynamicLoading(unittest.TestCase):
    """Exercises real importlib-based loading. The loader hardcodes the
    import path as `plugins.<type>.<module>`, so the plugins directory
    must actually be importable as a top-level `plugins` package — we
    build that layout in a temp dir and put the temp dir on sys.path,
    mirroring how it's used from a project's working directory."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir, ignore_errors=True)
        sys.path.insert(0, self.test_dir)
        self.addCleanup(self._remove_from_syspath)
        self.addCleanup(self._purge_plugins_modules)

        self.plugins_dir = Path(self.test_dir) / "plugins"
        (self.plugins_dir / "providers").mkdir(parents=True)
        (self.plugins_dir / "rotators").mkdir(parents=True)

    def _remove_from_syspath(self):
        if self.test_dir in sys.path:
            sys.path.remove(self.test_dir)

    def _purge_plugins_modules(self):
        # Namespace packages get cached in sys.modules across tests;
        # drop anything under `plugins` so the next test starts clean.
        for mod_name in list(sys.modules):
            if mod_name == "plugins" or mod_name.startswith("plugins."):
                del sys.modules[mod_name]

    def _write(self, relative_path: str, content: str):
        path = self.plugins_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_valid_provider_plugin_is_discovered_and_registered(self):
        self._write(
            "providers/hello_provider.py",
            """
from secret_rotator.providers.base import SecretProvider

class HelloProvider(SecretProvider):
    plugin_name = "hello"

    def get_secret(self, secret_id):
        return "value"

    def update_secret(self, secret_id, new_value):
        return True

    def validate_connection(self):
        return True
""",
        )

        loader = PluginLoader(plugins_dir=str(self.plugins_dir))
        loader.discover_and_load_plugins()

        provider_cls = loader.registry.get_provider("hello")
        self.assertIsNotNone(provider_cls)
        self.assertEqual(provider_cls.__name__, "HelloProvider")

    def test_files_starting_with_underscore_are_skipped(self):
        self._write(
            "providers/_helpers.py",
            """
from secret_rotator.providers.base import SecretProvider

class ShouldNotLoad(SecretProvider):
    plugin_name = "should_not_load"
    def get_secret(self, secret_id): return ""
    def update_secret(self, secret_id, new_value): return True
    def validate_connection(self): return True
""",
        )

        loader = PluginLoader(plugins_dir=str(self.plugins_dir))
        loader.discover_and_load_plugins()
        self.assertIsNone(loader.registry.get_provider("should_not_load"))

    def test_broken_plugin_file_does_not_crash_discovery(self):
        """A plugin with a syntax/import error must be logged and
        skipped, not take down the rest of plugin discovery."""
        self._write("providers/broken.py", "this is not valid python :::")
        self._write(
            "providers/good.py",
            """
from secret_rotator.providers.base import SecretProvider

class GoodProvider(SecretProvider):
    plugin_name = "good"
    def get_secret(self, secret_id): return ""
    def update_secret(self, secret_id, new_value): return True
    def validate_connection(self): return True
""",
        )

        loader = PluginLoader(plugins_dir=str(self.plugins_dir))
        try:
            loader.discover_and_load_plugins()
        except Exception as e:  # pragma: no cover - failure path we're guarding against
            self.fail(f"discover_and_load_plugins() raised instead of isolating the failure: {e}")

        self.assertIsNotNone(loader.registry.get_provider("good"))
        self.assertIsNone(loader.registry.get_provider("broken"))

    def test_rotator_plugin_is_discovered_and_registered(self):
        self._write(
            "rotators/hello_rotator.py",
            """
from secret_rotator.rotators.base import SecretRotator

class HelloRotator(SecretRotator):
    plugin_name = "hello_rotator"

    def generate_new_secret(self):
        return "generated"

    def validate_secret(self, secret):
        return bool(secret)
""",
        )

        loader = PluginLoader(plugins_dir=str(self.plugins_dir))
        loader.discover_and_load_plugins()

        rotator_cls = loader.registry.get_rotator("hello_rotator")
        self.assertIsNotNone(rotator_cls)
        self.assertEqual(rotator_cls.__name__, "HelloRotator")

    def test_plugin_without_plugin_name_falls_back_to_lowercased_class_name(self):
        self._write(
            "providers/unnamed_provider.py",
            """
from secret_rotator.providers.base import SecretProvider

class UnnamedProvider(SecretProvider):
    def get_secret(self, secret_id): return ""
    def update_secret(self, secret_id, new_value): return True
    def validate_connection(self): return True
""",
        )

        loader = PluginLoader(plugins_dir=str(self.plugins_dir))
        loader.discover_and_load_plugins()
        self.assertIsNotNone(loader.registry.get_provider("unnamedprovider"))


if __name__ == "__main__":
    unittest.main()
