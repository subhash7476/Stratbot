"""
Filter registry - Plugin system for signal quality filters.
"""

from typing import Dict, Type, List
import logging

from core.filters.base import BaseSignalFilter


logger = logging.getLogger(__name__)


class FilterRegistry:
    """
    Registry for signal quality filters.

    Filters register themselves by name, allowing dynamic instantiation
    from configuration without hardcoded imports.
    """

    _filters: Dict[str, Type[BaseSignalFilter]] = {}

    @classmethod
    def register(cls, name: str, filter_class: Type[BaseSignalFilter]) -> None:
        """
        Register a filter class.

        Args:
            name: Unique name for the filter (e.g., 'kalman', 'volatility')
            filter_class: The filter class (must inherit from BaseSignalFilter)

        Raises:
            ValueError: If name already registered or invalid class
        """
        if name in cls._filters:
            logger.warning(f"Filter '{name}' already registered, overwriting")

        if not issubclass(filter_class, BaseSignalFilter):
            raise ValueError(f"{filter_class} must inherit from BaseSignalFilter")

        cls._filters[name] = filter_class
        logger.info(f"Registered filter: {name} -> {filter_class.__name__}")

    @classmethod
    def get(cls, name: str) -> Type[BaseSignalFilter]:
        """
        Get a filter class by name.

        Args:
            name: Filter name

        Returns:
            Filter class

        Raises:
            KeyError: If filter not found
        """
        if name not in cls._filters:
            raise KeyError(
                f"Filter '{name}' not found. Available filters: {list(cls._filters.keys())}"
            )
        return cls._filters[name]

    @classmethod
    def list_available(cls) -> List[str]:
        """Get list of all registered filter names."""
        return list(cls._filters.keys())

    @classmethod
    def create(cls, name: str, config: Dict) -> BaseSignalFilter:
        """
        Create a filter instance by name.

        Args:
            name: Filter name (must be registered)
            config: Filter configuration

        Returns:
            Instantiated filter

        Raises:
            KeyError: If filter not found
        """
        filter_class = cls.get(name)
        return filter_class(config=config, filter_name=name)
