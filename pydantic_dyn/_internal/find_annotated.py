from typing import Annotated, get_type_hints, get_origin, get_args


def find_annotated_metadata_for_iterative(obj):
    """
    Traverses type hints of an object iteratively to find all
    Annotated metadata, regardless of nesting depth.
    """
    results = []
    stack = []

    # Initialize stack with top-level hints
    try:
        # include_extras=True is required to see Annotated metadata
        hints = get_type_hints(obj, include_extras=True)
        stack.extend(hints.values())
    except Exception:
        # If the object itself is a type, start with it
        stack.append(obj)

    visited = set()

    while stack:
        current = stack.pop()

        # Guard against circular/recursive types to prevent infinite loops
        try:
            if current in visited:
                continue
            visited.add(current)
        except TypeError:
            pass  # Some type hints (like list[int]) may not be hashable

        origin = get_origin(current)
        args = get_args(current)

        # Check if the current type is an Annotated wrapper
        if origin is Annotated:
            # Annotated args format: (InnerType, Metadata1, Metadata2, ...)
            results.append({
                "type": args[0],
                "metadata": args[1:]
            })
            # Add the inner type to the stack to check for deeper nesting
            stack.append(args[0])

        # If it's a generic (e.g., list[T], dict[K, V]), check its arguments
        elif args:
            stack.extend(args)

    return results
