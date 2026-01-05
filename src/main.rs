use gtk::prelude::*;
use gtk::{Application, ApplicationWindow, glib, Button};
use serde_json::json;
use reqwest::Client;

const APP_ID: &str = "org.gtk_rs.ani-gtk";

const QUERY: &str = "
query ($type: MediaType!, $userName: String!,$status_in: [MediaListStatus!]) {
  MediaListCollection(type: $type, userName: $userName, status_in:$status_in) {
    lists {
      name
      entries {
        id
        media {
          id
          title {
            romaji
          }
        }
      }
    }
  }
}
";

#[tokio::main]
async fn main() {
    let client = Client::new();
    // Define query and variables
    let json = json!({"query": QUERY, "variables": {"type":"ANIME","userName": "Wairen","status_in":"CURRENT"}});
    // Make HTTP post request
    let resp = client.post("https://graphql.anilist.co/")
                .header("Content-Type", "application/json")
                .header("Accept", "application/json")
                .body(json.to_string())
                .send()
                .await
                .unwrap()
                .text()
                .await;
    // Get json
    let result: serde_json::Value = serde_json::from_str(&resp.unwrap()).unwrap();
    println!("{}",result["data"]["MediaListCollection"]["lists"][0]["name"]);
    println!("{}",result["data"]["MediaListCollection"]["lists"][0]["entries"]);
}

/*
fn main() -> glib::ExitCode {
    // Create a new application
    let app = Application::builder().application_id(APP_ID).build();

    // Connect to "activate" signal of `app`
    app.connect_activate(build_ui);

    // Run the application
    app.run()
}

fn build_ui(app: &Application) {

    // Create a button with label and margins
    let button = Button::builder()
        .label("Press me!")
        .margin_top(12)
        .margin_bottom(12)
        .margin_start(12)
        .margin_end(12)
        .build();

    // Connect to "clicked" signal of `button`
    button.connect_clicked(|button| {
        // Set the label to "Hello World!" after the button has been clicked on
        button.set_label("Hello World!");
    });


    // Create a window and set the title
    let window = ApplicationWindow::builder()
        .application(app)
        .title("ani-gtk")
        .child(&button)
        .build();
    window.set_default_size(640,480);

    // Present window
    window.present();
}
*/