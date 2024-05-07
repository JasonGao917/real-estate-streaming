import asyncio
import json

from bs4 import BeautifulSoup
from openai import OpenAI
from playwright.async_api import async_playwright
from kafka import KafkaProducer

SBR_WS_CDP = ''
BASE_URL = "https://zoopla.co.uk"
LOCATION = "London"

client = OpenAI(api_key="")

def extract_property_details(input):
    print("Extracting property details")
    command = """
        You are a data extractor model and you have been tasked with extracting information about the apartment for me into json.
        Here is the div for the property details:
        
        {input_command}
        
        this is the final json structure expected:
        {{
            "price": "",
            "address": "",
            "bedrooms": "",
            "bathrooms": "",
            "receptions": "",
            "EPC Rating": "",
            "tenure": "",
            "time_remaining_on_lease": "",
            "service_charge": "",
            "council_tax_band": "",
            "ground_rent": ""
        }}
    """.format(input_command=input)

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {
                "role": "user",
                "content": command
            }
        ]
    )

    res = response.choices[0].message.content
    json_data = json.loads(res)

    return json_data

def extract_floor_plan(soup):
    print("Extracting floor plan")

    plan = {}
    floor_plan = soup.find('div', {"data-testid": "floorplan-thumbnail-0"})
    if floor_plan:
        floor_plan_src = floor_plan.find("picture").find("source")['srcset']
        plan["floor_plan"] = floor_plan_src.split(' ')[0]

    return plan

def extract_picture(picture_section):
    picture_sources = []
    for picture in picture_section.find_all("picture"):
        for source in picture.find_all("source"):
            source_type = source.get("type", "").split("/")[-1]
            pic_url = source.get("srcset", "").split(",")[0].split(" ")[0]

            if source_type == "webp" and '1024' in pic_url:
                picture_sources.append(pic_url)

    return picture_sources

async def run(pw, producer):
    print('Connecting to Scraping Browser')
    browser = await pw.chromium.connect_over_cdp(SBR_WS_CDP)
    try:
        page = await browser.new_page()
        print(f'Connected! Navigating to {BASE_URL}')
        print(BASE_URL)
        await page.goto(BASE_URL)
        
        await page.fill('input[name="autosuggest-input"]', LOCATION)
        await page.keyboard.press("Enter")
        print("Waiting for search results")
        await page.wait_for_load_state("load")
        
        content = await page.inner_html('div[data-testid="regular-listings"]')
        soup = BeautifulSoup(content, "html.parser")        
        
        for _, div in enumerate(soup.find_all("div", class_="dkr2t83")):
            link = div.find('a')['href']
            data = {
                "link": BASE_URL + link,
                "title": div.find("h2").text,
                "address": div.find('address').text,
            }       
            
            # goto the listing page
            print("Navigating to the listing page:", link)
            await page.goto(data['link'])
            await page.wait_for_load_state("load")   

            content = await page.inner_html('div[data-testid="listing-details-page"]')
            soup = BeautifulSoup(content, "html.parser")

            # get pictures
            picture_section = soup.find("section", {"aria-labelledby": "listing-gallery-heading"})
            pictures = extract_picture(picture_section)
            data['pictures'] = pictures
            
            # get property details
            property_details = soup.select_one('div[class="_14bi3x34l _14bi3x331"]')
            property_details = extract_property_details(property_details)  
            
            floor_plan = extract_floor_plan(soup)

            data.update(floor_plan)
            data.update(property_details)       
            
            print("Sending data to Kafka")
            producer.send("properties", value=json.dumps(data).encode('utf-8'))
            print("Data sent to Kafka")
    finally:
        await browser.close()


async def main():
    producer = KafkaProducer(bootstrap_servers=["localhost:9092"], max_block_ms=5000)
    async with async_playwright() as playwright:
        await run(playwright, producer)


if __name__ == '__main__':
    asyncio.run(main())