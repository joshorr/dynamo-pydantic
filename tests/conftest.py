import os

os.environ.update(dict(
    APP_ENV='unittest',
    AWS_ACCESS_KEY_ID='testing',
    AWS_SECRET_ACCESS_KEY='testing',
    AWS_SECURITY_TOKEN='testing',
    AWS_SESSION_TOKEN='testing',
))
