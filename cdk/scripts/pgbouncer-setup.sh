#!/bin/bash
set -euxo pipefail

# These variables will be replaced by the TypeScript code
SECRET_ARN=${SECRET_ARN}
REGION=${REGION}
POOL_MODE=${POOL_MODE}
MAX_CLIENT_CONN=${MAX_CLIENT_CONN}
DEFAULT_POOL_SIZE=${DEFAULT_POOL_SIZE}
MIN_POOL_SIZE=${MIN_POOL_SIZE}
RESERVE_POOL_SIZE=${RESERVE_POOL_SIZE}
RESERVE_POOL_TIMEOUT=${RESERVE_POOL_TIMEOUT}
MAX_DB_CONNECTIONS=${MAX_DB_CONNECTIONS}
MAX_USER_CONNECTIONS=${MAX_USER_CONNECTIONS}
CLOUDWATCH_CONFIG="/opt/aws/amazon-cloudwatch-agent/bin/config.json"

# Add the postgres repository
curl https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
sudo sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'

# Install required packages
apt-get update

wait_for_dpkg_lock() {
  while fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock >/dev/null 2>&1; do
    echo "Waiting for dpkg lock to be released..."
    sleep 2
  done
}

wait_for_dpkg_lock
DEBIAN_FRONTEND=noninteractive apt-get install -y pgbouncer jq awscli

echo "Fetching secret from ARN: ${SECRET_ARN}"

# Before handling secrets, turn off command tracing
set +x
SECRET=$(aws secretsmanager get-secret-value --secret-id ${SECRET_ARN} --region ${REGION} --query SecretString --output text)

# Parse database credentials without echoing
DB_HOST=$(echo "$SECRET" | jq -r '.host')
DB_PORT=$(echo "$SECRET" | jq -r '.port')
DB_NAME=$(echo "$SECRET" | jq -r '.dbname')
DB_USER=$(echo "$SECRET" | jq -r '.username')
DB_PASSWORD=$(echo "$SECRET" | jq -r '.password')

echo 'Creating PgBouncer configuration...'

# Create pgbouncer.ini
cat <<EOC > /etc/pgbouncer/pgbouncer.ini
[databases]
* = host=$DB_HOST port=$DB_PORT dbname=$DB_NAME

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 5432
auth_type = md5
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = ${POOL_MODE}
max_client_conn = ${MAX_CLIENT_CONN}
default_pool_size = ${DEFAULT_POOL_SIZE}
min_pool_size = ${MIN_POOL_SIZE}
reserve_pool_size = ${RESERVE_POOL_SIZE}
reserve_pool_timeout = ${RESERVE_POOL_TIMEOUT}
max_db_connections = ${MAX_DB_CONNECTIONS}
max_user_connections = ${MAX_USER_CONNECTIONS}
max_prepared_statements = 10
ignore_startup_parameters = application_name,search_path
logfile = /var/log/pgbouncer/pgbouncer.log
pidfile = /var/run/pgbouncer/pgbouncer.pid
admin_users = $DB_USER
stats_users = $DB_USER
log_connections = 1
log_disconnections = 1
log_pooler_errors = 1
log_stats = 1
stats_period = 60
EOC

# Create userlist.txt without echoing sensitive info
{
    echo "\"$DB_USER\" \"$DB_PASSWORD\""
} > /etc/pgbouncer/userlist.txt

# Turn command tracing back on
set -x

# Set correct permissions
chown postgres:postgres /etc/pgbouncer/pgbouncer.ini /etc/pgbouncer/userlist.txt
chmod 600 /etc/pgbouncer/pgbouncer.ini /etc/pgbouncer/userlist.txt

# Configure logging
mkdir -p /var/log/pgbouncer /var/run/pgbouncer
chown postgres:postgres /var/log/pgbouncer /var/run/pgbouncer
chmod 755 /var/log/pgbouncer /var/run/pgbouncer

touch /var/log/pgbouncer/pgbouncer.log
chown postgres:postgres /var/log/pgbouncer/pgbouncer.log
chmod 640 /var/log/pgbouncer/pgbouncer.log

# Enable and start pgbouncer service
systemctl enable pgbouncer
systemctl restart pgbouncer


cat <<EOC > /etc/logrotate.d/pgbouncer
/var/log/pgbouncer/pgbouncer.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    copytruncate
    create 640 postgres postgres
}
EOC

# Create monitoring scripts directory
mkdir -p /opt/pgbouncer/scripts

# Create the health check script
cat <<'EOC' > /opt/pgbouncer/scripts/check-pgbouncer.sh
#!/bin/bash
if ! pgrep pgbouncer > /dev/null; then
    systemctl start pgbouncer
    echo 'PgBouncer was down, restarted' | logger -t pgbouncer-monitor
fi
EOC
chmod +x /opt/pgbouncer/scripts/check-pgbouncer.sh

# Create a single crontab file
cat <<'EOC' > /opt/pgbouncer/scripts/crontab.txt
# PgBouncer health check - run every minute
* * * * * /opt/pgbouncer/scripts/check-pgbouncer.sh
# PgBouncer metrics collection - run every minute
* * * * * /opt/pgbouncer/scripts/pgbouncer-metrics.sh
EOC

# Install the crontab as the root user
crontab /opt/pgbouncer/scripts/crontab.txt

# Verify the crontab was installed
if ! crontab -l; then
    echo 'Failed to install crontab' | logger -t pgbouncer-setup
    exit 1
fi

# Create per-boot script directory
mkdir -p /var/lib/cloud/scripts/per-boot

# Create per-boot script
cat <<'EOF' > /var/lib/cloud/scripts/per-boot/00-run-config.sh
#!/bin/bash
# Stop existing services
systemctl stop pgbouncer

# Re-run configuration
curl -o /tmp/user-data http://169.254.169.254/latest/user-data
chmod +x /tmp/user-data
/tmp/user-data
EOF

chmod +x /var/lib/cloud/scripts/per-boot/00-run-config.sh

# Create CloudWatch configuration directory
mkdir -p /opt/pgbouncer/cloudwatch

# Install CloudWatch agent
if ! wget -q https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb; then
    echo 'Failed to download CloudWatch agent' | logger -t pgbouncer-setup
    exit 1
fi

if ! dpkg -i amazon-cloudwatch-agent.deb; then
    echo 'Failed to install CloudWatch agent' | logger -t pgbouncer-setup
    exit 1
fi

# Create CloudWatch config
cat <<EOC > ${CLOUDWATCH_CONFIG}
{
  "agent": {
    "metrics_collection_interval": 60,
    "run_as_user": "root"
  },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/pgbouncer/pgbouncer.log",
            "log_group_name": "/pgbouncer/logs",
            "log_stream_name": "{instance_id}",
            "timestamp_format": "%Y-%m-%d %H:%M:%S",
            "multi_line_start_pattern": "{timestamp_format}",
            "retention_in_days": 14
          },
          {
            "file_path": "/var/log/syslog",
            "log_group_name": "/pgbouncer/system-logs",
            "log_stream_name": "{instance_id}",
            "timestamp_format": "%b %d %H:%M:%S",
            "retention_in_days": 14
          }
        ]
      }
    }
  },
  "metrics": {
    "metrics_collected": {
      "procstat": [
        {
          "pattern": "pgbouncer",
          "measurement": [
            "cpu_usage",
            "memory_rss",
            "read_bytes",
            "write_bytes",
            "read_count",
            "write_count",
            "num_fds"
          ]
        }
      ],
      "mem": {
        "measurement": [
          "mem_used_percent"
        ]
      },
      "disk": {
        "measurement": [
          "used_percent"
        ]
      }
    },
    "aggregation_dimensions": [["InstanceId"]]
  }
}
EOC

# Verify the config file exists
if [ ! -f ${CLOUDWATCH_CONFIG} ]; then
    echo 'CloudWatch config file not created' | logger -t pgbouncer-setup
    exit 1
fi

# Start CloudWatch agent
if ! /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -s -c file:${CLOUDWATCH_CONFIG}; then
    echo 'Failed to configure CloudWatch agent' | logger -t pgbouncer-setup
    exit 1
fi

systemctl enable amazon-cloudwatch-agent
systemctl start amazon-cloudwatch-agent

# Verify CloudWatch agent is running
if ! systemctl is-active amazon-cloudwatch-agent; then
    echo 'CloudWatch agent failed to start' | logger -t pgbouncer-setup
    exit 1
fi

