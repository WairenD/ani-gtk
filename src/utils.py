# utils.py
from html.parser import HTMLParser

class HTMLInterpretor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []
    
    def handle_starttag(self, tag, attrs):
        if tag == 'br':
            self.result.append('\n')
    
    def handle_data(self, data):
        self.result.append(data)
    
    def get_result(self):
        return ''.join(self.result)

def interpret_html(html):
    interpretor = HTMLInterpretor()
    interpretor.feed(html)
    return interpretor.get_result()

def format_date(date_dict):
    if date_dict and all(value is not None for value in date_dict.values()):
        return f"{date_dict['year']}-{date_dict['month']}-{date_dict['day']}"
    return 'N/A'