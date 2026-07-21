from processors.step13_index_generator import canonicalize_name, canonicalize_tag_names


def test_tag_alias_conversion_and_chain():
    aliases = {"サクマ": "佐久間", "佐久間さん": "サクマ"}
    assert canonicalize_name("佐久間さん", aliases) == "佐久間"
    assert canonicalize_tag_names(["サクマ", "佐久間", "佐久間さん"], aliases) == ["佐久間"]

