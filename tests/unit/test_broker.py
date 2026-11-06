from shared.messaging.broker import InMemoryBroker, partition_for


def test_same_key_same_partition_and_order_preserved():
    b = InMemoryBroker(3)
    for i in range(10):
        b.publish("t", "R-1", str(i).encode())
        b.publish("t", "R-2", str(i).encode())
    for key in ("R-1", "R-2"):
        msgs = [m for m in b.messages("t") if m.key == key]
        assert len({m.partition for m in msgs}) == 1
        assert [int(m.value) for m in msgs] == list(range(10))
    assert partition_for("R-1", 3) == partition_for("R-1", 3)


def test_uncommitted_message_is_redelivered_and_group_offsets_independent():
    b = InMemoryBroker(1)
    b.publish("t", "k", b"x")
    c1, c2 = b.consumer("g1", "t"), b.consumer("g2", "t")
    m = c1.poll()
    assert c1.poll() == m          # not committed -> redelivered
    c1.commit(m)
    assert c1.poll() is None
    assert c2.poll() == m          # other group unaffected
