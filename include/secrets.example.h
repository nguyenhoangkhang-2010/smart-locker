// Copy to secrets.h (git-ignored) and replace the value.
// Generate one:  python -c "import secrets; print('sl-' + secrets.token_urlsafe(18))"
// Must equal MQTT_TOPIC_PREFIX in backend/.env (and cam/include/cam_config.h).
#ifndef SECRETS_H
#define SECRETS_H

#define MQTT_TOPIC_PREFIX "CHANGE-ME-long-random-string"

#endif
