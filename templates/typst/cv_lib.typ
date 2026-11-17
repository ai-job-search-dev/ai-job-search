// Shared Typst helpers for CV and cover letter. Set rules live in the importing document.
#let primary_color = rgb("#1a365d")
#let text_muted = rgb("#4a5568")

#let cv_header(name: "", title: "", email: "", phone: "", location: "", linkedin: "", github: "") = {
  align(center)[
    #text(size: 18pt, weight: "bold", fill: primary_color)[#name] \
    #v(-2pt)
    #text(size: 11pt, weight: "medium", fill: text_muted)[#title] \
    #v(2pt)
    #text(size: 9pt)[
      #location
      #if email != "" [ • #link("mailto:" + email)[#email] ]
      #if phone != "" [ • #phone ]
      #if linkedin != "" [ • #link(linkedin)[LinkedIn] ]
      #if github != "" [ • #link(github)[GitHub] ]
    ]
  ]
  v(8pt)
  line(length: 100%, stroke: 0.8pt + rgb("#cbd5e1"))
  v(4pt)
}

#let cv_section(title) = {
  v(8pt)
  text(size: 12pt, weight: "bold", fill: primary_color)[#upper(title)]
  v(-3pt)
  line(length: 100%, stroke: 0.5pt + rgb("#94a3b8"))
  v(4pt)
}

#let cv_entry(title: "", subtitle: "", location: "", date: "", description: []) = {
  grid(
    columns: (1fr, auto),
    text(weight: "bold")[#title] + if subtitle != "" [ \ #text(style: "italic", fill: text_muted)[#subtitle] ],
    align(right)[
      #text(weight: "bold", fill: primary_color)[#date] \
      #if location != "" [ #text(style: "italic", size: 8.5pt, fill: text_muted)[#location] ]
    ]
  )
  v(1pt)
  description
  v(4pt)
}


#let letter_header(name: "", contact: "") = {
  text(size: 16pt, weight: "bold", fill: primary_color)[#name]
  linebreak()
  text(size: 9pt, fill: text_muted)[#contact]
  v(6pt)
  line(length: 100%, stroke: 0.8pt + rgb("#cbd5e1"))
  v(10pt)
}
