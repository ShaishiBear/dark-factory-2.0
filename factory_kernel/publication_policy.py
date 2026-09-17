"""Protected single-project publication destination; never supplied by a model or PR.

Since WP01 these constants are read from the trusted project profile
(`.factory/project-profile.json` through `project_profile.current_profile`). They keep their
names and their values so every existing reader and every test that patches them keeps
working; they are wrappers over the profile, not a second source of identity. A second
project is a second profile selected by trusted configuration, never a request parameter.
"""
from .project_profile import current_profile

_PROFILE = current_profile()

REPOSITORY = _PROFILE.repository
OWNER = _PROFILE.owner
PROJECT = _PROFILE.project
ORIGIN = _PROFILE.publication_origin
APP_LOGIN = _PROFILE.app_login
WORKFLOW = _PROFILE.publication_workflow
WORKFLOW_PATH = _PROFILE.publication_workflow_path
ARTIFACT = _PROFILE.publication_artifact
BRANCH_PREFIX = _PROFILE.programme_branch_prefix
