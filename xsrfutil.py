#!/usr/bin/python2.5
#
# Copyright 2010 the Melange authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Helper methods for creating & verifying XSRF tokens for AppEngine."""

__authors__ = [
  '"Doug Coker" <dcoker@google.com>',
  '"Joe Gregorio" <jcgregorio@google.com>',
  'dfa@websec.at',
]


import base64
import binascii
import hmac
import os  # for urandom
import time

from google.appengine.api import memcache
from google.appengine.api import users
from google.appengine.ext import db

# String used instead of user id when there is no user. Not that it makes sense
# to protect unauthenticated functionality from XSRF.
ANONYMOUS_USER = 'anonymous'

# Delimiter character
DELIMITER = ':'

# 24 hours in seconds
DEFAULT_TIMEOUT_SECS = 1*60*60*24

def generate_token(key, user_id, path="", when=None):
  """Generates a URL-safe token for the given user, action, time tuple.

  Args:
    key: secret key to use.
    user_id: the user ID of the authenticated user.
    path: The path the token should be valid for.
    when: the time in seconds since the epoch at which the user was
      authorized for this action. If not set the current time is used.

  Returns:
    A string XSRF protection token.
  """
  when = when or int(time.time())
  digester = hmac.new(str(key))
  digester.update(str(user_id))
  digester.update(DELIMITER)
  digester.update(str(path))
  digester.update(DELIMITER)
  digester.update(str(when))
  digest = digester.digest()

  token = base64.urlsafe_b64encode('%s%s%d' % (digest,
                                               DELIMITER,
                                               when))
  return token


def validate_token(key, token, user_id, path="", current_time=None,
                   timeout=DEFAULT_TIMEOUT_SECS):
  """Validates that the given token authorizes the user for the action.

  Tokens are invalid if the time of issue is too old or if the token
  does not match what generateToken outputs (i.e. the token was forged).

  Args:
    key: secret key to use.
    token: a string of the token generated by generateToken.
    user_id: the user ID of the authenticated user.
    path: The path the token was received on.
    current_time: Time at which the token was received (defaults to now)
    timeout: How long your tokens are valid in seconds before they time out
      (defaults to DEFAULT_TIMEOUT_SECS)

  Returns:
    A boolean - True if the user is authorized for the action, False
    otherwise.
  """
  if not token:
    return False
  try:
    decoded = base64.urlsafe_b64decode(str(token))
    token_time = long(decoded.split(DELIMITER)[-1])
  except (TypeError, ValueError):
    return False
  if current_time is None:
    current_time = time.time()
  # If the token is too old it's not valid.
  if current_time - token_time > timeout:
    return False

  # The given token should match the generated one with the same time.
  expected_token = generate_token(key, user_id, path=path, when=token_time)
  return const_time_compare(expected_token, token)


def const_time_compare(a, b):
  """Compares the the given strings in constant time."""
  if len(a) != len(b):
    return False

  equals = 0
  for x, y in zip(a, b):
    equals |= ord(x) ^ ord(y)

  return equals == 0


def xsrf_protect(func):
  """Decorator to protect webapp2's get and post functions from XSRF.

  Decorating a function with @xsrf_protect will verify that a valid XSRF token
  has been submitted through the xsrf parameter. Both GET and POST parameters
  are accepted.

  If no token or an invalid token is received, the decorated function is not
  called and a 403 error will be issued.
  """
  def decorate(self, *args, **kwargs):
    path = os.environ.get('PATH_INFO', '/')
    token = self.request.get('xsrf', None)
    if not token:
      self.error(403)
      return

    user = ANONYMOUS_USER
    if users.get_current_user():
      user = users.get_current_user().user_id()
    if not validate_token(XsrfSecret.get(), token, user, path):
      self.error(403)
      return

    return func(self, *args, **kwargs)

  return decorate


def xsrf_token(path=None):
  """Generates an XSRF token for the given path.

  This function is mostly supposed to be used as a filter for a templating
  system, so that tokens can be conveniently generated directly in the
  template.

  Args:
    path: The path the token should be valid for. By default, the path of the
      current request.
  """
  if not path:
    path = os.environ.get('PATH_INFO')
  user = ANONYMOUS_USER
  if users.get_current_user():
    user = users.get_current_user().user_id()
  return generate_token(XsrfSecret.get(), user, path)


class XsrfSecret(db.Model):
  """Model for datastore to store the XSRF secret."""
  secret = db.StringProperty(required=True)

  @staticmethod
  def get():
    """Retrieves the XSRF secret.

    Tries to retrieve the XSRF secret from memcache, and if that fails, falls
    back to getting it out of datastore. Note that the secret should not be
    changed, as that would result in all issued tokens becoming invalid.
    """
    secret = memcache.get('xsrf_secret')
    if not secret:
      xsrf_secret = XsrfSecret.all().get()
      if not xsrf_secret:
        # hmm, nothing found? We need to generate a secret for xsrf protection.
        secret = binascii.b2a_hex(os.urandom(16))
        xsrf_secret = XsrfSecret(secret=secret)
        xsrf_secret.put()

      secret = xsrf_secret.secret
      memcache.set('xsrf_secret', secret)

    return secret
