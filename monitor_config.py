"""Utility functions for loading and saving Tasmania Parliament Monitor configuration."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

CONFIG_PATH = Path("config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "database": {
        "path": "tasmania_parliament.db"
    },
    "sources": {
        "urls": {
            "house_members": "https://www.parliament.tas.gov.au/house-of-assembly/house-members",
            "house_tabled": "https://www.parliament.tas.gov.au/house-of-assembly/tabled-papers-2025",
            "house_register": "https://www.parliament.tas.gov.au/house-of-assembly/register-of-members-interests",
            "lc_members": "https://www.parliament.tas.gov.au/legislative-council/current-members",
            "lc_tabled": "https://www.parliament.tas.gov.au/legislative-council/tpp",
            "lc_register": "https://www.parliament.tas.gov.au/legislative-council/register-of-members-interests",
            "committees_ha": "https://www.parliament.tas.gov.au/house-of-assembly/committees",
            "committees_lc": "https://www.parliament.tas.gov.au/legislative-council/committees",
            "committees_joint": "https://www.parliament.tas.gov.au/parliamentary-committees/current-committees",
            "standing_orders_ha": "https://www.parliament.tas.gov.au/house-of-assembly/standing-orders",
            "standing_orders_lc": "https://www.parliament.tas.gov.au/legislative-council/standing-orders",
            "bills": "https://www.parliament.tas.gov.au/bills/bills-by-year",
            "hansard": "https://www.parliament.tas.gov.au/hansard",
            "papers_search": "https://search.parliament.tas.gov.au"
        }
    },
    "scraping": {
        "timeout": 30,
        "retry_attempts": 3,
        "retry_delay": 5,
        "user_agent": "Tasmania Parliament Monitor Bot 1.0"
    },
    "monitoring": {
        "frequencies": {
            "tabled_papers": 15,
            "members": 60,
            "committees": 30,
            "standing_orders": 120,
            "bills": 30,
            "hansard": 15
        }
    },
    "notifications": {
        "email": {
            "enabled": False,
            "smtp_server": "smtp.gmail.com",
            "smtp_port": 587,
            "from_address": "your-email@example.com",
            "password": "your-app-password",
            "recipients": ["recipient@example.com"]
        }
    },
    "keywords": {
        "gaming_gambling": ["gaming", "casino", "wagering", "betting", "gambling", "lottery", "pokies", "electronic gaming"],
        "infrastructure": ["infrastructure", "construction", "roads", "bridges", "public works", "capital projects", "development"],
        "environment": ["environment", "climate", "emissions", "pollution", "conservation", "renewable", "sustainability", "waste"],
        "health": ["health", "hospital", "medical", "healthcare", "mental health", "aged care", "ambulance"],
        "business_economy": ["business", "economy", "tax", "budget", "fiscal", "investment", "employment", "industry", "tourism"],
        "planning": ["planning", "zoning", "land use", "development", "heritage", "building", "subdivision"],
        "aboriginal_affairs": ["aboriginal", "indigenous", "reconciliation", "native title", "cultural heritage"]
    },
    "alerts": {
        "critical_keywords": ["urgent", "immediate", "emergency", "crisis", "mandatory", "compliance", "penalty", "enforcement"],
        "high_priority_sources": [
            "Premier",
            "Treasurer",
            "Attorney-General",
            "Minister for Health",
            "Minister for Infrastructure"
        ]
    },
    "dashboard": {
        "refresh_interval_seconds": 120,
        "logic_progression": {
            "order": ["overview", "documents", "alerts", "members", "committees", "watchlist", "reports"],
            "rules": {
                "documents": {
                    "depends_on": ["overview"],
                    "description": "Requires summary statistics to contextualise document stream"
                },
                "alerts": {
                    "depends_on": ["documents"],
                    "description": "Alert list prioritised once new documents ingested"
                },
                "members": {
                    "depends_on": ["overview"],
                    "description": "Member activity follows high-level monitoring status"
                },
                "committees": {
                    "depends_on": ["members"],
                    "description": "Committee tracking requires member context"
                },
                "watchlist": {
                    "depends_on": ["alerts"],
                    "description": "Keyword adjustments informed by alert review"
                },
                "reports": {
                    "depends_on": ["watchlist"],
                    "description": "Reports generated once monitoring inputs tuned"
                }
            }
        }
    }
}


def merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge two dictionaries without mutating inputs."""
    result = deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def load_config(path: Path | str = CONFIG_PATH) -> Dict[str, Any]:
    """Load configuration from disk, creating defaults if necessary."""
    config_path = Path(path)
    if not config_path.exists():
        save_config(DEFAULT_CONFIG, config_path)
        return deepcopy(DEFAULT_CONFIG)

    with config_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    merged = merge_dict(DEFAULT_CONFIG, data)
    return merged


def save_config(config: Dict[str, Any], path: Path | str = CONFIG_PATH) -> None:
    """Persist configuration to disk."""
    config_path = Path(path)
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")


def get_dashboard_logic(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Extract dashboard logic progression rules from the configuration."""
    cfg = config or load_config()
    return deepcopy(cfg.get("dashboard", {}).get("logic_progression", {}))

__all__ = ["load_config", "save_config", "get_dashboard_logic", "DEFAULT_CONFIG", "CONFIG_PATH"]
