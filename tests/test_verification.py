import secrets,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from app.services.auth_service import AuthService,AuthError,Unauthorized

class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.auth=AuthService(Path(self.tmp.name)/'accounts.enc',secrets.token_bytes(32));self.auth.mailer=Mock();self.auth.mailer.configured.return_value=True
        self.data=dict(email='person@example.com',name='Person',password='unique registration password')
    def tearDown(self):self.tmp.cleanup()
    def start(self):
        result=self.auth.start_registration(self.data);code=self.auth.mailer.send_code.call_args.args[1];return result,code
    def test_only_code_hash_is_stored_and_replay_fails(self):
        result,code=self.start();self.assertNotIn('code',result);self.assertNotIn(code,str(result))
        user=self.auth.login(self.data);self.assertEqual(user['email_verified'],0)
        with self.assertRaises(Unauthorized):self.auth.session(user)
        with self.auth.conn() as c:
            row=c.execute('SELECT * FROM email_codes').fetchone();self.assertNotEqual(row['code_mac'],code);self.assertNotEqual(row['challenge'],result['challenge_id'])
        verified=self.auth.verify_email(result['challenge_id'],code);self.assertEqual(verified['email_verified'],1);self.auth.session(verified)
        with self.assertRaises(AuthError):self.auth.verify_email(result['challenge_id'],code)
    def test_attempts_commit_and_expired_codes_fail(self):
        result,code=self.start();wrong='000000' if code!='000000' else '111111'
        for _ in range(5):
            with self.assertRaises(AuthError):self.auth.verify_email(result['challenge_id'],wrong)
        with self.assertRaisesRegex(AuthError,'Лимит'):self.auth.verify_email(result['challenge_id'],code)
        with self.auth.conn() as c:self.assertEqual(c.execute('SELECT attempts FROM email_codes').fetchone()[0],5)
    def test_resend_replaces_old_code_and_has_cooldown(self):
        with patch('app.services.auth_service.time.time',return_value=1800000000):
            result,code=self.start()
            with self.assertRaises(AuthError):self.auth.resend_verification(result['challenge_id'])
        with patch('app.services.auth_service.time.time',return_value=1800000061):new=self.auth.resend_verification(result['challenge_id'])
        with self.assertRaises(AuthError):self.auth.verify_email(result['challenge_id'],code)
        newcode=self.auth.mailer.send_code.call_args.args[1]
        with patch('app.services.auth_service.time.time',return_value=1800000700):
            with self.assertRaisesRegex(AuthError,'истёк'):self.auth.verify_email(new['challenge_id'],newcode)
    def test_no_fake_registration_when_mail_is_unconfigured(self):
        self.auth.mailer.configured.return_value=False
        with self.assertRaises(AuthError):self.auth.start_registration(self.data)
        with self.auth.conn() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM users').fetchone()[0],0)
