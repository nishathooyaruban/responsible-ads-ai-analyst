from google.ads.googleads.client import GoogleAdsClient


def main():
    print("Connecting to Google Ads...")

    client = GoogleAdsClient.load_from_storage(
        "google-ads.yaml"
    )

    print("Google Ads client loaded successfully!")

    ga_service = client.get_service("GoogleAdsService")

    query = """
        SELECT
            customer.id,
            customer.descriptive_name
        FROM customer
        LIMIT 1
    """

    response = ga_service.search(
        customer_id=client.login_customer_id,
        query=query,
    )

    for row in response:
        print("Customer ID:", row.customer.id)
        print("Customer Name:", row.customer.descriptive_name)


if __name__ == "__main__":
    main()