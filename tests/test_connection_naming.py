"""Regression test for the run/mask base_directory mutual-corruption bug.

Both commands used to derive a connection name from the bucket only, so DataMasque
(which matches connections by name and ignores base_directory) would silently reuse
one command's connection for the other — masking the wrong prefix. The name must
now depend on the base_directory too.
"""

from masque_bricks.cli import _connection_name


def test_different_base_directories_give_different_names():
    raw = _connection_name("source", "my-bucket", "raw")
    masked = _connection_name("source", "my-bucket", "masked")
    assert raw != masked


def test_run_and_mask_prefixes_do_not_collide():
    # `run` uses base_directory "raw"; `mask` may pass "raw/users".
    run_name = _connection_name("source", "my-bucket", "raw")
    mask_name = _connection_name("source", "my-bucket", "raw/users")
    assert run_name != mask_name


def test_slug_collisions_stay_distinct():
    # "raw/users" and "raw-users" slugify identically; the digest must keep them apart.
    assert _connection_name("source", "b", "raw/users") != _connection_name("source", "b", "raw-users")


def test_role_separates_source_and_dest():
    assert _connection_name("source", "b", "raw") != _connection_name("dest", "b", "raw")


def test_name_is_slugified():
    name = _connection_name("source", "my-bucket", "raw/users/2024")
    assert " " not in name and "/" not in name and "-" not in name
    assert name.startswith("masque_bricks_source_")
