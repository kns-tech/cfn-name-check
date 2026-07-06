"""cfn-name-check: worst-case AWS resource name length checker for CloudFormation."""
__version__ = "0.2.1"
from .engine import check_template  # noqa: F401
