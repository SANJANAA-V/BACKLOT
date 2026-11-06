from shared.messaging.kafka_io import create_topics
from shared.utilities.config import get_settings

if __name__ == "__main__":
    s = get_settings()
    create_topics(s.kafka_bootstrap_servers, [s.topic_events, s.topic_dlq], s.topic_partitions)
    print("topics ready")
