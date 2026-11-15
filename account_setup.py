"""Interactive local OAuth setup; never called by the model."""
import argparse
import json
from pathlib import Path
import re
import webbrowser

import accounts


def main():
    parser = argparse.ArgumentParser(description='Sign local Jarvis into email and calendar accounts.')
    parser.add_argument('provider', choices=['google', 'microsoft'])
    args = parser.parse_args()
    if args.provider == 'google':
        from google_auth_oauthlib.flow import InstalledAppFlow
        client = accounts.CONFIG / 'google-client.json'
        if not client.is_file():
            print('Google requires an OAuth desktop-app client for this local app.')
            print('Enable the Gmail and Google Calendar APIs in your Google Cloud project.')
            print('Configure Google Auth consent for your own account, then create a Desktop app client.')
            print('Download its JSON credentials. Do not paste credentials into chat.')
            webbrowser.open('https://console.cloud.google.com/auth/clients')
            path = Path(input('Path to downloaded desktop client JSON (blank to exit): ').strip()).expanduser()
            if not path.is_file():
                raise SystemExit('No client file selected. No account connected.')
            data = json.loads(path.read_text())
            installed = data.get('installed', {})
            if (not installed.get('client_id') or
                installed.get('auth_uri') != 'https://accounts.google.com/o/oauth2/auth' or
                installed.get('token_uri') != 'https://oauth2.googleapis.com/token'):
                raise SystemExit('Expected an official Google Desktop app client JSON.')
            accounts.save_private(client, json.dumps(data))
        flow = InstalledAppFlow.from_client_secrets_file(str(client), accounts.GOOGLE_SCOPES,
                                                       autogenerate_code_verifier=True)
        credentials = flow.run_local_server(host='127.0.0.1', port=0, timeout_seconds=180,
                                           authorization_prompt_message='Opening Google sign-in in your browser.',
                                           success_message='Jarvis sign-in complete. You may close this tab.')
        if not credentials.has_scopes(accounts.GOOGLE_SCOPES):
            raise SystemExit('Email and calendar read permissions were not both granted. Reconnect to retry.')
        accounts.save_private(accounts.CONFIG / 'google-token.json', credentials.to_json())
    else:
        client = accounts.CONFIG / 'microsoft-client.json'
        if not client.is_file():
            print('Microsoft requires an app registration for this local app.')
            print('Allow personal and work/school accounts, enable public client flows, and add')
            print('delegated Microsoft Graph Mail.Read and Calendars.Read permissions.')
            webbrowser.open('https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade')
            client_id = input('Application (client) ID (blank to exit): ').strip()
            if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', client_id):
                raise SystemExit('No valid client ID entered. No account connected.')
            accounts.save_private(client, json.dumps({'client_id': client_id}))
        app, cache = accounts.microsoft_app()
        flow = app.initiate_device_flow(scopes=accounts.MICROSOFT_SCOPES)
        if 'user_code' not in flow:
            raise SystemExit('Microsoft could not start sign-in. Check app registration settings.')
        print(flow['message'])
        webbrowser.open(flow['verification_uri'])
        result = app.acquire_token_by_device_flow(flow)
        if 'access_token' not in result:
            raise SystemExit('Microsoft sign-in did not complete. No account connected.')
        accounts.save_private(accounts.CONFIG / 'microsoft-cache.json', cache.serialize())
    print(f'{args.provider.title()} connected to local Jarvis for email and calendar reading.')
    print('Try: check my email; show my calendar; check my Outlook email.')


if __name__ == '__main__':
    main()
