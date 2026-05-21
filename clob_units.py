def parse_clob_units(raw_value: object, field_name: str, response: object) -> int:
    if isinstance(raw_value, bool):
        raise RuntimeError(f"CLOB balance response has invalid {field_name}: {response!r}")

    if isinstance(raw_value, int):
        if raw_value < 0:
            raise RuntimeError(f"CLOB balance response has negative {field_name}: {response!r}")
        return raw_value

    if isinstance(raw_value, str):
        if not raw_value.isdecimal():
            raise RuntimeError(f"CLOB balance response has invalid {field_name}: {response!r}")
        return int(raw_value)

    raise RuntimeError(f"CLOB balance response has invalid {field_name}: {response!r}")
