"""SMTP verification mail. Codes never enter logs, API responses or local mail files."""
import os
import re
import smtplib
import ssl
from email.message import EmailMessage

class Mailer:
    def configured(self):return bool(os.environ.get('SUPPLY_MATCH_SMTP_HOST') and os.environ.get('SUPPLY_MATCH_SMTP_FROM'))
    def send_code(self,email,code,*,reset=False):
        if not self.configured():raise ValueError('Отправка писем ещё не настроена владельцем сервера. Нужны SMTP_HOST и SMTP_FROM; регистрация без подтверждения отключена.')
        sender=os.environ['SUPPLY_MATCH_SMTP_FROM']
        if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',sender):raise ValueError('Некорректный адрес отправителя SMTP.')
        try:port=int(os.environ.get('SUPPLY_MATCH_SMTP_PORT','465'))
        except ValueError:raise ValueError('Некорректный порт SMTP.') from None
        message=EmailMessage();message['From']=sender;message['To']=email;message['Subject']='Восстановление пароля Supply Match' if reset else 'Код подтверждения Supply Match'
        message.set_content(('Код восстановления пароля: ' if reset else 'Ваш код подтверждения: ')+code+'\n\nКод действует 10 минут. Никому не сообщайте его.\nЕсли вы не создавали аккаунт, проигнорируйте это письмо.\n')
        context=ssl.create_default_context();host=os.environ['SUPPLY_MATCH_SMTP_HOST'];mode=os.environ.get('SUPPLY_MATCH_SMTP_SECURITY','ssl')
        if mode not in ('ssl','starttls'):raise ValueError('SMTP должен использовать ssl или starttls.')
        try:
            client=smtplib.SMTP_SSL(host,port,timeout=15,context=context) if mode=='ssl' else smtplib.SMTP(host,port,timeout=15)
            with client:
                if mode=='starttls':client.ehlo();client.starttls(context=context);client.ehlo()
                username=os.environ.get('SUPPLY_MATCH_SMTP_USER','')
                if username:client.login(username,os.environ.get('SUPPLY_MATCH_SMTP_PASSWORD',''))
                refused=client.send_message(message)
                if refused:raise smtplib.SMTPException('refused')
        except (OSError,UnicodeError,smtplib.SMTPException):raise ValueError('Почтовый сервер не принял письмо. Попробуйте позже или обратитесь к владельцу сервиса.') from None
