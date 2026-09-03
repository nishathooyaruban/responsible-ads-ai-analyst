from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = ["https://www.googleapis.com/auth/adwords"]


def main():
    flow = InstalledAppFlow.from_client_secrets_file(
        "client_secret.json",
        SCOPES
    )

    credentials = flow.run_local_server(port=8090)

    print("\n====================================")
    print("REFRESH TOKEN")
    print("====================================")
    print(credentials.refresh_token)
    print("====================================")


if __name__ == "__main__":
    main()