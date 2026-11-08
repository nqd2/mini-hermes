from open_webui.mini_hermes.provenance import content_version, evidence_id


def test_resource_version_and_evidence_change_when_source_changes():
    original = content_version('one')
    changed = content_version('two')

    assert original != changed
    assert evidence_id('file-1', original, 0, 3, 'one') != evidence_id(
        'file-1', changed, 0, 3, 'two'
    )


def test_evidence_id_is_stable_for_the_same_version_and_range():
    version = content_version('whole document')
    assert evidence_id('file-1', version, 10, 20, 'section') == evidence_id(
        'file-1', version, 10, 20, 'section'
    )
    assert evidence_id('file-1', version, 10, 20, 'section') != evidence_id(
        'file-1', version, 20, 30, 'section'
    )
