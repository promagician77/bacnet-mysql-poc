/* ===========================================================================
   BACnet trend store - phase 1

   Three things in here are deliberate and worth a sentence each, because they
   are the ones that are painful to retrofit once a site is logging:

   1. statusFlags travels with every reading. BACnet hands you present_value
      alongside in-alarm / fault / overridden / out-of-service. A reading taken
      while a point is overridden or out of service is not plant data, and if
      you discard the flags at write time you can never tell the difference
      afterwards. Almost every naive schema drops them.

   2. Readings record where they came from. A value that arrived by COV and one
      that arrived by reconciliation poll are not equally trustworthy, and when
      a trend looks wrong the first question is always which path produced it.

   3. The primary key is (object_id, read_at). InnoDB clusters rows on the
      primary key, so one point's history is physically contiguous and a trend
      query is a range scan rather than a scatter. An AUTO_INCREMENT id with a
      secondary index gets this backwards.
   =========================================================================== */

CREATE TABLE site (
    site_id        SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code           VARCHAR(32)  NOT NULL,
    name           VARCHAR(128) NOT NULL,
    timezone       VARCHAR(64)  NOT NULL DEFAULT 'UTC',
    PRIMARY KEY (site_id),
    UNIQUE KEY uq_site_code (code)
) ENGINE=InnoDB;

/* A BACnet device instance is unique per site, not globally. */
CREATE TABLE bacnet_device (
    device_id       INT UNSIGNED NOT NULL AUTO_INCREMENT,
    site_id         SMALLINT UNSIGNED NOT NULL,
    instance        INT UNSIGNED NOT NULL,        -- device object instance
    name            VARCHAR(128),
    vendor_id       SMALLINT UNSIGNED,
    model_name      VARCHAR(128),
    address         VARCHAR(64),                  -- 10.1.4.22:47808, or MSTP mac
    segmentation    ENUM('both','transmit','receive','none'),
    max_apdu        SMALLINT UNSIGNED,
    last_seen_at    DATETIME(3),
    PRIMARY KEY (device_id),
    UNIQUE KEY uq_device (site_id, instance),
    CONSTRAINT fk_device_site FOREIGN KEY (site_id) REFERENCES site (site_id)
) ENGINE=InnoDB;

/* One row per BACnet object we collect. object_name is the engineer's label and
   it does change - keep the identifier as the key, never the name. */
CREATE TABLE bacnet_object (
    object_id       INT UNSIGNED NOT NULL AUTO_INCREMENT,
    device_id       INT UNSIGNED NOT NULL,
    object_type     SMALLINT UNSIGNED NOT NULL,   -- 0 AI, 1 AO, 2 AV, 3 BI, ...
    instance        INT UNSIGNED NOT NULL,
    object_name     VARCHAR(128),
    description     VARCHAR(255),
    units           SMALLINT UNSIGNED,            -- BACnet engineering-units enum
    cov_increment   DECIMAL(12,4),
    collect_mode    ENUM('cov','poll','cov_poll') NOT NULL DEFAULT 'cov_poll',
    poll_seconds    SMALLINT UNSIGNED NOT NULL DEFAULT 600,
    is_active       TINYINT(1) NOT NULL DEFAULT 1,
    PRIMARY KEY (object_id),
    UNIQUE KEY uq_object (device_id, object_type, instance),
    KEY ix_object_active (is_active, collect_mode),
    CONSTRAINT fk_object_device FOREIGN KEY (device_id) REFERENCES bacnet_device (device_id)
) ENGINE=InnoDB;

/* The fact table. Deliberately narrow - this is the one that grows.
   Partitioned by month so dropping aged data is a DDL operation rather than a
   DELETE that has to walk the table. */
CREATE TABLE reading (
    object_id      INT UNSIGNED NOT NULL,
    read_at        DATETIME(3)  NOT NULL,
    value          DOUBLE       NULL,             -- NULL where the read errored
    source         ENUM('cov','poll','backfill') NOT NULL,
    in_alarm       TINYINT(1) NOT NULL DEFAULT 0,
    fault          TINYINT(1) NOT NULL DEFAULT 0,
    overridden     TINYINT(1) NOT NULL DEFAULT 0,
    out_of_service TINYINT(1) NOT NULL DEFAULT 0,
    reliability    SMALLINT UNSIGNED NULL,        -- BACnet reliability enum
    PRIMARY KEY (object_id, read_at),
    KEY ix_reading_time (read_at)
) ENGINE=InnoDB
PARTITION BY RANGE COLUMNS (read_at) (
    PARTITION p2026_04 VALUES LESS THAN ('2026-05-01'),
    PARTITION p2026_05 VALUES LESS THAN ('2026-06-01'),
    PARTITION p2026_06 VALUES LESS THAN ('2026-07-01'),
    PARTITION pmax     VALUES LESS THAN (MAXVALUE)
);

/* The watchdog. Without this the failure mode is silence, and silence from a
   collector looks exactly like a building where nothing is changing. */
CREATE TABLE collector_heartbeat (
    site_id         SMALLINT UNSIGNED NOT NULL,
    collector_name  VARCHAR(64) NOT NULL,
    beat_at         DATETIME(3) NOT NULL,
    version         VARCHAR(32),
    points_active   INT UNSIGNED,
    cov_since_last  INT UNSIGNED,
    polls_since_last INT UNSIGNED,
    errors_since_last INT UNSIGNED,
    PRIMARY KEY (site_id, collector_name),
    CONSTRAINT fk_beat_site FOREIGN KEY (site_id) REFERENCES site (site_id)
) ENGINE=InnoDB;

/* Which points have gone quiet, and for how long. This is the query that turns
   a silent failure into an alert - run it on a schedule, alarm on the top. */
CREATE OR REPLACE VIEW point_staleness AS
SELECT  s.code                               AS site_code,
        d.instance                           AS device_instance,
        o.object_id,
        o.object_name,
        MAX(r.read_at)                       AS last_reading_at,
        TIMESTAMPDIFF(MINUTE, MAX(r.read_at), NOW()) AS minutes_stale,
        o.poll_seconds / 60                  AS expected_within_minutes
FROM        bacnet_object o
JOIN        bacnet_device d ON d.device_id = o.device_id
JOIN        site          s ON s.site_id   = d.site_id
LEFT JOIN   reading       r ON r.object_id = o.object_id
WHERE       o.is_active = 1
GROUP BY    s.code, d.instance, o.object_id, o.object_name, o.poll_seconds
HAVING      last_reading_at IS NULL
        OR  minutes_stale > (o.poll_seconds / 60) * 3;

/* Trend query this schema is shaped for: one point, one window, range scan on
   the clustered key.

   SELECT read_at, value
   FROM   reading
   WHERE  object_id = ?
     AND  read_at >= ? AND read_at < ?
     AND  fault = 0 AND out_of_service = 0
   ORDER BY read_at;
*/
